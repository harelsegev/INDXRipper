"""
    Find index entries in $INDEX_ALLOCATION folder attributes
    Author: Harel Segev
    05/16/2021
"""
__version__ = "3.0.1"

import argparse
from sys import stderr
from datetime import timezone, datetime
from contextlib import suppress

from ntfs import parse_filename_attribute, get_resident_attribute, get_attribute_name, get_attribute_type
from ntfs import is_valid_fixup, is_valid_record_signature, get_attribute_headers
from ntfs import EmptyNonResidentAttributeError, get_non_resident_attribute, is_directory
from ntfs import get_mft_chunks, get_record_headers, apply_fixup, get_sequence_number
from ntfs import get_boot_sector, get_mft_data_attribute, get_base_record_reference, is_base_record

from indx import find_index_entries


class NoFilenameAttributeInRecordError(ValueError):
    pass


def get_arguments():
    parser = argparse.ArgumentParser(prog="INDXRipper",
                                     description="find index entries in $INDEX_ALLOCATION attributes")

    parser.add_argument("image", metavar="image", help="image file path")
    parser.add_argument("outfile", metavar="outfile", help="output file path")
    parser.add_argument("-V", "--version", action='version', version=f"%(prog)s {__version__}")

    parser.add_argument("-m", metavar="MOUNT_POINT", default="",
                        help="a name to display as the mount point of the image, e.g. C:")

    parser.add_argument("-o", metavar="OFFSET", type=int, default=0, help="offset to an NTFS partition (in sectors)")
    parser.add_argument("-b", metavar="SECTOR_SIZE", type=int, default=512, help="sector size in bytes. default is 512")
    parser.add_argument("-w", choices=["csv", "bodyfile"], default="csv", help="output format. default is csv")

    parser.add_argument("--invalid-only", action="store_true",
                        help="only display entries with an invalid file reference")

    parser.add_argument("--dedup", action="store_true", help="deduplicate output lines")
    return parser.parse_args()


def eprint(*args, **kwargs):
    print(*args, file=stderr, **kwargs)


def warning(message):
    eprint(f"INDXRipper: warning: {message}")


def get_parent_reference(filename_attribute):
    parent_reference = filename_attribute["ParentDirectoryReference"]
    return parent_reference["FileRecordNumber"], parent_reference["SequenceNumber"]


def get_filename_attribute(mft_chunk, attribute_header):
    return parse_filename_attribute(get_resident_attribute(mft_chunk, attribute_header))


def is_directory_index_allocation(attribute_header):
    return get_attribute_type(attribute_header) == "INDEX_ALLOCATION" and get_attribute_name(attribute_header) == "$I30"


def add_to_mft_values(vbr, raw_image, mft_chunk, attribute_header, values):
    if get_attribute_type(attribute_header) == "FILE_NAME":
        values["$FILE_NAME"].append(get_filename_attribute(mft_chunk, attribute_header))

    elif is_directory_index_allocation(attribute_header):
        values["$INDEX_ALLOCATION"].append(get_non_resident_attribute(vbr, raw_image, mft_chunk, attribute_header))


def get_mft_dict_values(vbr, raw_image, mft_chunk, record_header):
    values = {"$FILE_NAME": [], "$INDEX_ALLOCATION": []}
    if is_directory(record_header):
        for attribute_header in get_attribute_headers(mft_chunk, record_header):
            with suppress(EmptyNonResidentAttributeError):
                add_to_mft_values(vbr, raw_image, mft_chunk, attribute_header, values)

    return values


def get_mft_records(mft_data, vbr):
    current_record = -1
    for mft_chunk in get_mft_chunks(vbr, mft_data):
        record_headers = get_record_headers(mft_chunk, vbr)
        apply_fixup(mft_chunk, record_headers, vbr)

        for record_header in record_headers:
            current_record += 1
            if is_valid_record_signature(record_header):
                if not is_valid_fixup(record_header):
                    warning(f"fixup verification failed for file record at index {current_record}")
                    continue

                yield current_record, get_sequence_number(record_header), mft_chunk, record_header


def add_to_mft_dict(mft_dict, key, values, base_record):
    if key not in mft_dict:
        mft_dict[key] = values
        mft_dict[key]["HAS_BASE_RECORD"] = base_record
    else:
        mft_dict[key]["$INDEX_ALLOCATION"] += values["$INDEX_ALLOCATION"]
        mft_dict[key]["$FILE_NAME"] += values["$FILE_NAME"]
        mft_dict[key]["HAS_BASE_RECORD"] = mft_dict[key]["HAS_BASE_RECORD"] or base_record


def get_mft_dict(raw_image, mft_data, vbr):
    mft_dict = dict()
    for index, sequence, mft_chunk, record_header in get_mft_records(mft_data, vbr):
        values = get_mft_dict_values(vbr, raw_image, mft_chunk, record_header)
        if is_base_record(record_header):
            add_to_mft_dict(mft_dict, (index, sequence), values, True)
        else:
            base_reference = get_base_record_reference(record_header)
            add_to_mft_dict(mft_dict, base_reference, values, False)

    return mft_dict


NAMESPACE_PRIORITY = {"DOS": 0, "POSIX": 1, "WIN32_DOS": 2, "WIN32": 3}


def get_filename_priority(filename):
    return NAMESPACE_PRIORITY[filename["FilenameNamespace"]]


def get_first_filename(mft_dict, key):
    if mft_dict[key]["$FILE_NAME"]:
        return max(mft_dict[key]["$FILE_NAME"], key=get_filename_priority)
    else:
        raise NoFilenameAttributeInRecordError


path_cache = {(5, 5): ""}


def get_path_helper(mft_dict, key):
    if key in path_cache:
        return path_cache[key]

    elif key not in mft_dict:
        path_cache[key] = "/$Orphan"

    else:
        try:
            filename = get_first_filename(mft_dict, key)
            parent_reference = get_parent_reference(filename)
            path_cache[key] = get_path_helper(mft_dict, parent_reference) + "/" + filename["FilenameInUnicode"]

        except NoFilenameAttributeInRecordError:
            path_cache[key] = "/$NoName/[FileNumber: {}, SequenceNumber: {}]".format(*key)

    return path_cache[key]


def get_path(mft_dict, key, mount_point):
    return mount_point + get_path_helper(mft_dict, key)


def to_epoch(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).timestamp()


def to_iso(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).isoformat()


COMMON_FIELDS = {
    "index": lambda index_entry: index_entry["FILE_REFERENCE"]["FileRecordNumber"],
    "sequence": lambda index_entry: index_entry["FILE_REFERENCE"]["SequenceNumber"],

    "size": lambda index_entry: index_entry["RealSize"],
    "alloc_size": lambda index_entry: index_entry["AllocatedSize"],

    "cr_time": lambda index_entry: index_entry["CreationTime"],
    "m_time": lambda index_entry: index_entry["LastModificationTime"],
    "a_time": lambda index_entry: index_entry["LastAccessTime"],
    "c_time": lambda index_entry: index_entry["LastMftChangeTime"],
}

OUTPUT_FORMATS = {
    "csv":
    {
        "fmt": "\"{full_path}\",{flags},{index},{sequence},{size},{alloc_size},{cr_time},{m_time},{a_time},{c_time}\n",
        "header": "Path,Flags,FileNumber,SequenceNumber,Size,AllocatedSize,CreationTime,ModificationTime,AccessTime,"
                  "ChangeTime\n",

        "fields":
        {
            "flags": lambda index_entry: "|".join
            (
                [flag for flag in index_entry["Flags"] if index_entry["Flags"][flag] and flag != "_flagsenum"]
            )

        } | COMMON_FIELDS,

        "adapted_fields": {"cr_time": to_iso, "m_time": to_iso, "a_time": to_iso, "c_time": to_iso}
    },

    "bodyfile":
    {
        "fmt": "0|{full_path} ($I30)|{index}|{mode_prt1}{mode_prt2}|0|0|{size}|{a_time}|{m_time}|{c_time}|{cr_time}\n",
        "header": "",

        "fields":
        {
            "mode_prt1": lambda index_entry: "d/-" if index_entry["Flags"]["DIRECTORY"] else "r/-",
            "mode_prt2": lambda index_entry: 3 * "{}{}{}".format
            (
              "r" if not index_entry["Flags"]["READ_ONLY"] else "-",
              "w" if not index_entry["Flags"]["HIDDEN"] else "-",
              "x"
            )

        } | COMMON_FIELDS,

        "adapted_fields": {"cr_time": to_epoch, "m_time": to_epoch, "a_time": to_epoch, "c_time": to_epoch},
    }
}


def populate_fmt_dict(fmt_dict, index_entry, output_format):
    output_fields = OUTPUT_FORMATS[output_format]["fields"]
    adapted_fields = OUTPUT_FORMATS[output_format]["adapted_fields"]

    for field in output_fields:
        fmt_dict[field] = output_fields[field](index_entry)

        if field in adapted_fields:
            fmt_dict[field] = adapted_fields[field](fmt_dict[field])


def get_entry_output(index_entry, parent_path, output_format):
    fmt_dict = {
        "full_path": parent_path + "/" + index_entry["FilenameInUnicode"]
    }

    populate_fmt_dict(fmt_dict, index_entry, output_format)
    return OUTPUT_FORMATS[output_format]["fmt"].format(**fmt_dict)


def get_mft_key(index_entry):
    return index_entry["FILE_REFERENCE"]["FileRecordNumber"], index_entry["FILE_REFERENCE"]["SequenceNumber"]


def get_collection(dedup):
    if dedup:
        return set(), set.add
    else:
        return list(), list.append


def get_record_output(mft_dict, index_entries, parent_path, invalid_only, dedup, output_format):
    lines, add_line = get_collection(dedup)

    for index_entry in index_entries:
        mft_key = get_mft_key(index_entry)

        if not invalid_only or mft_key not in mft_dict or not mft_dict[mft_key]["HAS_BASE_RECORD"]:
            line = get_entry_output(index_entry, parent_path, output_format)
            add_line(lines, line)

    return lines


def get_output_lines(mft_dict, vbr, root_name, invalid_only, dedup, output_format):
    yield [OUTPUT_FORMATS[output_format]["header"]]

    for key in mft_dict:
        for index_allocation in mft_dict[key]["$INDEX_ALLOCATION"]:
            index_entries = find_index_entries(index_allocation, *key, vbr)
            parent_path = get_path(mft_dict, key, root_name)
            yield get_record_output(mft_dict, index_entries, parent_path, invalid_only, dedup, output_format)


def main():
    args = get_arguments()
    with open(args.image, "rb") as raw_image:
        vbr = get_boot_sector(raw_image, args.o * args.b)
        mft_data = get_mft_data_attribute(vbr, raw_image)
        mft_dict = get_mft_dict(raw_image, mft_data, vbr)

        with open(args.outfile, 'at+', encoding='utf-8') as outfile:
            for lines in get_output_lines(mft_dict, vbr, args.m, args.invalid_only, args.dedup, args.w):
                outfile.writelines(lines)


if __name__ == '__main__':
    main()
