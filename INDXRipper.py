"""
    Find index entries in $INDEX_ALLOCATION folder attributes
    Author: Harel Segev
    05/16/2021
"""
from ntfs import *
from indx import *
import argparse
from datetime import datetime, timedelta, timezone


class EmptyNameInFilenameAttribute(ValueError):
    pass


def get_arguments():
    parser = argparse.ArgumentParser(prog="INDXRipper",
                                     description="find index entries in $INDEX_ALLOCATION attributes")
    parser.add_argument("volume", metavar="volume", help=r"path to NTFS volume")
    parser.add_argument("outfile", metavar="outfile", help=r"output file path")
    parser.add_argument("-m", metavar="MOUNT_POINT", default="",
                        help="a name to display as the mount point of the image, e.g., C:")
    parser.add_argument("--deleted-only", action="store_true",
                        help="only display entries with an invalid file reference")
    parser.add_argument("--bodyfile", action="store_true", help="bodyfile output. default is CSV")
    return parser.parse_args()


def get_filename_attributes(mft_cluster, record_header):
    for attribute_header in get_attribute_headers(mft_cluster, record_header):
        if get_attribute_type(attribute_header) == "FILE_NAME":
            try:
                yield parse_filename_attribute(get_resident_attribute(mft_cluster, attribute_header))
            except UnicodeDecodeError:
                continue


def get_parent_reference(filename_attribute):
    parent_reference = filename_attribute["ParentDirectoryReference"]
    return parent_reference["FileRecordNumber"], parent_reference["SequenceNumber"]


def get_filename_attribute_values(mft_cluster, record_header):
    parent_index, parent_sequence = 0, 0
    longest_filename, previous_longest_filename_length = "", -1
    for attribute in get_filename_attributes(mft_cluster, record_header):
        parent_index, parent_sequence = get_parent_reference(attribute)

        if attribute["FilenameLengthInCharacters"] > previous_longest_filename_length:
            longest_filename = attribute["FilenameInUnicode"]
            previous_longest_filename_length = attribute["FilenameLengthInCharacters"]

    if parent_index == 0:
        return dict()
    return {"PARENT_INDEX": parent_index, "PARENT_SEQUENCE": parent_sequence, "FILENAME": longest_filename}


def is_directory_index_allocation(attribute_header, mft_cluster):
    res = get_attribute_type(attribute_header) == "INDEX_ALLOCATION"
    return res and get_attribute_name(mft_cluster, attribute_header) == "$I30"


def get_index_allocation_attribute(vbr, raw_partition, mft_cluster, record_header):
    for attribute_header in get_attribute_headers(mft_cluster, record_header):
        if is_directory_index_allocation(attribute_header, mft_cluster):
            try:
                return get_non_resident_attribute(vbr, raw_partition, mft_cluster, attribute_header)
            except EmptyNonResidentAttributeError:
                continue


def get_mft_dict_values(vbr, raw_partition, mft_cluster, record_header):
    values = get_filename_attribute_values(mft_cluster, record_header)
    values["INDEX_ALLOCATION"] = get_index_allocation_attribute(vbr, raw_partition, mft_cluster, record_header)
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


def add_to_mft_dict(mft_dict, key, values: dict):
    if key in mft_dict:
        mft_dict[key].update(values)
    else:
        mft_dict[key] = values


def get_mft_dict(raw_partition, mft_data, vbr):
    mft_dict = dict()
    for index, sequence, mft_cluster, record_header in get_mft_records(mft_data, vbr):
        if values := get_mft_dict_values(vbr, raw_partition, mft_cluster, record_header):
            if is_base_record(record_header):
                add_to_mft_dict(mft_dict, (index, sequence), values)
            else:
                base_reference = get_base_record_reference(record_header)
                add_to_mft_dict(mft_dict, base_reference, values)
    return mft_dict


ROOT_KEY = (5, 5)


def get_path(mft_dict, key, cache, mount_point):
    current_key = key

    res = ""
    while current_key != ROOT_KEY:
        if current_key in cache:
            res = cache[current_key] + res
            break

        res = "/" + mft_dict[current_key]["FILENAME"] + res
        current_key = mft_dict[current_key]["PARENT_INDEX"], mft_dict[current_key]["PARENT_SEQUENCE"]
        if current_key not in mft_dict:
            res = "/$Orphan" + res
            break

    cache[key] = res
    return mount_point + cache[key]


def to_datetime(filetime):
    return datetime(1601, 1, 1) + timedelta(microseconds=(filetime / 10))


def to_epoch(filetime):
    return int(to_datetime(filetime).replace(tzinfo=timezone.utc).timestamp())


def to_iso(filetime):
    return to_datetime(filetime).replace(tzinfo=timezone.utc).isoformat()


def get_timestamps(filename_attribute, out_bodyfile):
    a_time, c_time = filename_attribute["LastAccessTime"], filename_attribute["LastMftChangeTime"]
    m_time, cr_time = filename_attribute["LastModificationTime"], filename_attribute["CreationTime"]

    if out_bodyfile:
        return to_epoch(a_time), to_epoch(c_time), to_epoch(m_time), to_epoch(cr_time)
    else:
        return to_iso(a_time), to_iso(c_time), to_iso(m_time), to_iso(cr_time)


def concatenate(parent_path, filename_attribute):
    return f"{parent_path}/{filename_attribute['FilenameInUnicode']}"


def get_output_by_format(filename_attribute, parent_path, index, out_bodyfile):
    if not filename_attribute["FilenameLengthInCharacters"]:
        raise EmptyNameInFilenameAttribute

    full_path = concatenate(parent_path, filename_attribute)
    size, alloc_size = filename_attribute["RealSize"], filename_attribute["AllocatedSize"]
    a_time, c_time, m_time, cr_time = get_timestamps(filename_attribute, out_bodyfile)

    if out_bodyfile:
        return f"0|{full_path} ($I30)|{index}|------------|0|0|{size}|{a_time}|{m_time}|{c_time}|{cr_time}\n"
    else:
        return f"{full_path},{index},{size},{alloc_size},{cr_time},{m_time},{a_time},{c_time}\n"


def get_mft_key(index_entry):
    return index_entry["FILE_REFERENCE"]["FileRecordNumber"], index_entry["FILE_REFERENCE"]["SequenceNumber"]


def get_record_output(mft_dict, index_entries, parent_path, deleted_only, out_bodyfile):
    lines = list()
    for index_entry in index_entries:
        if (mft_key := get_mft_key(index_entry)) in mft_dict and deleted_only:
            continue
        try:
            line = get_output_by_format(index_entry["FILENAME_ATTRIBUTE"], parent_path, mft_key[0], out_bodyfile)
            lines.append(line)
        except (OverflowError, EmptyNameInFilenameAttribute):
            continue

    return lines


def init_line_list(out_bodyfile):
    lines = list()
    if not out_bodyfile:
        lines.append(f"Path,FileNumber,Size,AllocatedSize,CreationTime,ModificationTime,AccessTime,ChangeTime\n")
    return lines


def get_output_lines(mft_dict, vbr, root_name, out_bodyfile, deleted_only):
    cache = dict()
    lines = init_line_list(out_bodyfile)

    for key in mft_dict:
        if index_allocation := mft_dict[key]["INDEX_ALLOCATION"]:
            index_entries = find_index_entries(index_allocation, *key, vbr)
            parent_path = get_path(mft_dict, key, cache, root_name)
            lines += get_record_output(mft_dict, index_entries, parent_path, deleted_only, out_bodyfile)
    return lines


def main():
    args = get_arguments()
    with open(args.volume, "rb") as raw_partition:
        vbr = get_boot_sector(raw_partition)
        mft_data = get_mft_data_attribute(vbr, raw_partition)
        mft_dict = get_mft_dict(raw_partition, mft_data, vbr)

        with open(args.outfile, 'wt+', encoding='utf-8') as outfile:
            outfile.writelines(get_output_lines(mft_dict, vbr, args.m, args.bodyfile, args.deleted_only))


if __name__ == '__main__':
    main()
