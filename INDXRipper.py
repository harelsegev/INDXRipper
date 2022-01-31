"""
    Carve file metadata from NTFS $I30 indexes
    Author: Harel Segev
    05/16/2021
"""
__version__ = "5.2.0"

import argparse
from contextlib import suppress

from ntfs import parse_filename_attribute, get_resident_attribute, get_attribute_name, get_attribute_type
from ntfs import get_boot_sector, get_mft_data_attribute, get_base_record_reference, is_base_record
from ntfs import is_valid_fixup, is_valid_record_signature, get_attribute_headers, is_used
from ntfs import EmptyNonResidentAttributeError, get_non_resident_attribute, is_directory
from ntfs import get_mft_chunks, get_record_headers, apply_fixup, get_sequence_number

from indx import get_entries
from fmt import get_entry_output, get_format_header, warning


class NoFilenameAttributeInRecordError(ValueError):
    pass


def get_arguments():
    parser = argparse.ArgumentParser(prog="INDXRipper", description="carve file metadata from NTFS $I30 indexes")

    parser.add_argument("image", metavar="image", help="image file path")
    parser.add_argument("outfile", metavar="outfile", help="output file path")
    parser.add_argument("-V", "--version", action='version', version=f"%(prog)s {__version__}")

    parser.add_argument("-m", metavar="MOUNT_POINT", default="",
                        help="a name to display as the mount point of the image, e.g. C:")

    parser.add_argument("-o", metavar="OFFSET", type=int, default=0, help="offset to an NTFS partition (in sectors)")
    parser.add_argument("-b", metavar="SECTOR_SIZE", type=int, default=512, help="sector size in bytes. default is 512")
    parser.add_argument("-w", choices=["csv", "bodyfile"], default="csv", help="output format. default is csv")

    parser.add_argument("--deleted-dirs", action="store_true", help="display entries in deleted directories")
    parser.add_argument("--slack-only", action="store_true", help="only display entries in slack space")
    parser.add_argument("--dedup", action="store_true", help="deduplicate output lines")
    return parser.parse_args()


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
                    warning(f"fixup validation failed for file record at index {current_record}. ignoring this record")
                    continue

                yield current_record, get_sequence_number(record_header), mft_chunk, record_header


def add_to_mft_dict(mft_dict, key, values):
    if key not in mft_dict:
        mft_dict[key] = values
    else:
        mft_dict[key]["$INDEX_ALLOCATION"] += values["$INDEX_ALLOCATION"]
        mft_dict[key]["$FILE_NAME"] += values["$FILE_NAME"]


def get_mft_dict(raw_image, mft_data, deleted_dirs, vbr):
    mft_dict = dict()

    for index, sequence, mft_chunk, record_header in get_mft_records(mft_data, vbr):
        if is_directory(record_header):
            if deleted_dirs or is_used(record_header):
                values = get_mft_dict_values(vbr, raw_image, mft_chunk, record_header)

                if is_base_record(record_header):
                    add_to_mft_dict(mft_dict, (index, sequence), values)
                else:
                    base_reference = get_base_record_reference(record_header)
                    add_to_mft_dict(mft_dict, base_reference, values)

    return mft_dict


NAMESPACE_PRIORITY = {"DOS": 0, "WIN32_DOS": 1, "POSIX": 2, "WIN32": 3}


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
            path_cache[key] = "/$Orphan"

    return path_cache[key]


def get_path(mft_dict, key, mount_point):
    return mount_point + get_path_helper(mft_dict, key)


def get_collection(dedup):
    if dedup:
        return set(), set.add
    else:
        return list(), list.append


def get_record_output(index_entries, parent_path, dedup, output_format):
    lines, add_line = get_collection(dedup)

    for index_entry in index_entries:
        line = get_entry_output(index_entry, parent_path, output_format)
        add_line(lines, line)

    return lines


def get_output(mft_dict, vbr, root_name, slack_only, dedup, output_format):
    yield [get_format_header(output_format)]

    for key in mft_dict:
        if index_allocation_attributes := mft_dict[key]["$INDEX_ALLOCATION"]:
            parent_path = get_path(mft_dict, key, root_name)
            index_entries = get_entries(index_allocation_attributes, key, slack_only, vbr)
            yield get_record_output(index_entries, parent_path, dedup, output_format)


def main():
    args = get_arguments()
    with open(args.image, "rb") as raw_image:
        vbr = get_boot_sector(raw_image, args.o * args.b)
        mft_data = get_mft_data_attribute(vbr, raw_image)
        mft_dict = get_mft_dict(raw_image, mft_data, args.deleted_dirs, vbr)

        with open(args.outfile, "at+", encoding="utf-8") as outfile:
            for lines in get_output(mft_dict, vbr, args.m, args.slack_only, args.dedup, args.w):
                outfile.writelines(lines)


if __name__ == '__main__':
    main()
