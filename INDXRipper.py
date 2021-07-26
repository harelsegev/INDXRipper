"""
    Find index entries in $INDEX_ALLOCATION folder attributes
    Author: Harel Segev
    05/16/2021
"""
__version__ = "2.5.2"

from ntfs import *
from indx import *
import argparse
from datetime import datetime, timedelta, timezone
from contextlib import suppress


class EmptyNameInFilenameAttribute(ValueError):
    pass


class NoFilenameAttributeInRecord(ValueError):
    pass


def get_arguments():
    parser = argparse.ArgumentParser(prog="INDXRipper",
                                     description="find index entries in $INDEX_ALLOCATION attributes")
    parser.add_argument("image", metavar="image", help=r"image file path")
    parser.add_argument("outfile", metavar="outfile", help=r"output file path")
    parser.add_argument("-V", "--version", action='version', version=f"%(prog)s {__version__}")
    parser.add_argument("-m", metavar="MOUNT_POINT", default="",
                        help="a name to display as the mount point of the image, e.g., C:")
    parser.add_argument("-o", metavar="OFFSET", type=int, default=0,
                        help="offset to an NTFS partition, in sectors")
    parser.add_argument("-b", metavar="SECTOR_SIZE", type=int, default=512,
                        help="sector size in bytes. default is 512")
    parser.add_argument("--deleted-only", action="store_true",
                        help="only display entries with an invalid file reference")
    parser.add_argument("--bodyfile", action="store_true", help="bodyfile output. default is CSV")
    return parser.parse_args()


def get_filename_attributes(mft_cluster, record_header):
    for attribute_header in get_attribute_headers(mft_cluster, record_header):
        if get_attribute_type(attribute_header) == "FILE_NAME":
            with suppress(UnicodeError):
                yield parse_filename_attribute(get_resident_attribute(mft_cluster, attribute_header))


def get_parent_reference(filename_attribute):
    parent_reference = filename_attribute["ParentDirectoryReference"]
    return parent_reference["FileRecordNumber"], parent_reference["SequenceNumber"]


def get_filename_attribute_values(mft_cluster, record_header):
    for attribute in get_filename_attributes(mft_cluster, record_header):
        parent_index, parent_sequence = get_parent_reference(attribute)
        filename, namespace = attribute["FilenameInUnicode"], attribute["FilenameNamespace"]
        yield {"PARENT_REFERENCE": (parent_index, parent_sequence), "FILENAME": filename, "NAMESPACE": namespace}


def is_directory_index_allocation(attribute_header, mft_cluster):
    res = get_attribute_type(attribute_header) == "INDEX_ALLOCATION"
    return res and get_attribute_name(mft_cluster, attribute_header) == "$I30"


def get_index_allocation_attributes(vbr, raw_image, mft_cluster, record_header):
    for attribute_header in get_attribute_headers(mft_cluster, record_header):
        if is_directory_index_allocation(attribute_header, mft_cluster):
            yield get_non_resident_attribute(vbr, raw_image, mft_cluster, attribute_header)


def get_mft_dict_values(vbr, raw_image, mft_cluster, record_header):
    values = dict({"$FILE_NAME": [], "$INDEX_ALLOCATION": []})
    if is_directory(record_header):
        with suppress(NoFilenameAttributeInRecord):
            values["$FILE_NAME"] += get_filename_attribute_values(mft_cluster, record_header)

        with suppress(EmptyNonResidentAttributeError):
            values["$INDEX_ALLOCATION"] += get_index_allocation_attributes(vbr, raw_image, mft_cluster, record_header)

    return values


def get_mft_records(mft_data, vbr):
    current_record = -1
    for mft_cluster in get_mft_clusters(vbr, mft_data):
        mft_cluster = apply_file_record_fixup(mft_cluster, vbr)
        for record_header in get_record_headers(mft_cluster, vbr):
            current_record += 1
            if not record_header:
                continue

            yield current_record, get_sequence_number(record_header), mft_cluster, record_header


def add_to_mft_dict(mft_dict, key, values):
    if key not in mft_dict:
        mft_dict[key] = values
    else:
        mft_dict[key]["$INDEX_ALLOCATION"] += values["$INDEX_ALLOCATION"]
        mft_dict[key]["$FILE_NAME"] += values["$FILE_NAME"]


def get_mft_dict_helper(raw_image, mft_data, vbr):
    base_records = dict()
    extension_records = dict()

    for index, sequence, mft_cluster, record_header in get_mft_records(mft_data, vbr):
        values = get_mft_dict_values(vbr, raw_image, mft_cluster, record_header)
        if is_base_record(record_header):
            add_to_mft_dict(base_records, (index, sequence), values)
        else:
            base_reference = get_base_record_reference(record_header)
            add_to_mft_dict(extension_records, base_reference, values)

    return base_records, extension_records


def get_mft_dict(raw_image, mft_data, vbr):
    base_records, extension_records = get_mft_dict_helper(raw_image, mft_data, vbr)

    for base_reference in extension_records:
        if base_reference in base_records:
            add_to_mft_dict(base_records, base_reference, extension_records[base_reference])

    return base_records


NAMESPACE_PRIORITY = {"POSIX": 0, "DOS": 1, "WIN32_DOS": 2, "WIN32": 3}


def get_filename_priority(filename):
    return NAMESPACE_PRIORITY[filename["NAMESPACE"]]


def get_first_filename(mft_dict, key):
    return max(mft_dict[key]["$FILE_NAME"], key=get_filename_priority)


path_cache = dict({(5, 5): ""})


def get_path_helper(mft_dict, key):
    if key in path_cache:
        return path_cache[key]
    else:
        if key not in mft_dict:
            path_cache[key] = "$Orphan"

        else:
            filename = get_first_filename(mft_dict, key)
            path_cache[key] = get_path_helper(mft_dict, filename["PARENT_REFERENCE"]) + "/" + filename["FILENAME"]

        return path_cache[key]


def get_path(mft_dict, key, mount_point):
    return mount_point + get_path_helper(mft_dict, key)


def to_datetime(filetime):
    return datetime(1601, 1, 1) + timedelta(microseconds=(filetime / 10))


def to_epoch(filetime):
    return to_datetime(filetime).replace(tzinfo=timezone.utc).timestamp()


def to_iso(filetime):
    return to_datetime(filetime).replace(tzinfo=timezone.utc).isoformat()


def get_timestamps_by_format(filename_attribute, out_bodyfile):
    a_time, c_time = filename_attribute["LastAccessTime"], filename_attribute["LastMftChangeTime"]
    m_time, cr_time = filename_attribute["LastModificationTime"], filename_attribute["CreationTime"]

    if out_bodyfile:
        return to_epoch(a_time), to_epoch(c_time), to_epoch(m_time), to_epoch(cr_time)
    else:
        return to_iso(a_time), to_iso(c_time), to_iso(m_time), to_iso(cr_time)


def get_full_path(filename_attribute, parent_path):
    if not filename_attribute["FilenameLengthInCharacters"]:
        raise EmptyNameInFilenameAttribute

    return parent_path + "/" + filename_attribute["FilenameInUnicode"]


def get_file_size(filename_attribute):
    return filename_attribute["RealSize"], filename_attribute["AllocatedSize"]


def get_output_by_format(filename_attribute, parent_path, index, sequence, out_bodyfile):
    full_path = get_full_path(filename_attribute, parent_path)
    size, alloc_size = get_file_size(filename_attribute)
    a_time, c_time, m_time, cr_time = get_timestamps_by_format(filename_attribute, out_bodyfile)

    if out_bodyfile:
        return f"0|{full_path} ($I30)|{index}|------------|0|0|{size}|{a_time}|{m_time}|{c_time}|{cr_time}\n"
    else:
        return f'"{full_path}",{index},{sequence},{size},{alloc_size},{cr_time},{m_time},{a_time},{c_time}\n'


def get_mft_key(index_entry):
    return index_entry["FILE_REFERENCE"]["FileRecordNumber"], index_entry["FILE_REFERENCE"]["SequenceNumber"]


def get_entry_output(mft_dict, index_entry, parent_path, deleted_only, out_bodyfile):
    mft_key = get_mft_key(index_entry)
    if not deleted_only or mft_key not in mft_dict:
        return get_output_by_format(index_entry["FILENAME_ATTRIBUTE"], parent_path, *mft_key, out_bodyfile)


def get_record_output(mft_dict, index_entries, parent_path, deleted_only, out_bodyfile):
    lines = list()
    for index_entry in index_entries:
        with suppress(OverflowError, EmptyNameInFilenameAttribute):
            if line := get_entry_output(mft_dict, index_entry, parent_path, deleted_only, out_bodyfile):
                lines.append(line)

    return lines


def get_output_lines(mft_dict, vbr, root_name, out_bodyfile, deleted_only):
    if not out_bodyfile:
        yield "Path,FileNumber,SequenceNumber,Size,AllocatedSize,CreationTime,ModificationTime,AccessTime,ChangeTime\n"

    for key in mft_dict:
        for index_allocation in mft_dict[key]["$INDEX_ALLOCATION"]:
            index_entries = find_index_entries(index_allocation, *key, vbr)
            parent_path = get_path(mft_dict, key, root_name)
            yield get_record_output(mft_dict, index_entries, parent_path, deleted_only, out_bodyfile)


def main():
    args = get_arguments()
    with open(args.image, "rb") as raw_image:
        vbr = get_boot_sector(raw_image, args.o * args.b)
        mft_data = get_mft_data_attribute(vbr, raw_image)
        mft_dict = get_mft_dict(raw_image, mft_data, vbr)

        with open(args.outfile, 'at+', encoding='utf-8') as outfile:
            for lines in get_output_lines(mft_dict, vbr, args.m, args.bodyfile, args.deleted_only):
                outfile.writelines(lines)


if __name__ == '__main__':
    main()
