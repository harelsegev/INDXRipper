"""
    Carve file metadata from NTFS $I30 indexes
    Author: Harel Segev
    05/16/2021
"""
__version__ = "20231117"

from contextlib import suppress
import argparse

from ntfs import parse_filename_attribute, get_resident_attribute, get_attribute_name, get_attribute_type
from ntfs import get_boot_sector, get_first_mft_data_attribute, get_base_record_reference, is_base_record
from ntfs import apply_record_fixup, get_attribute_headers, is_used, EmptyNonResidentAttributeError
from ntfs import get_mft_chunks, get_record_headers, get_attribute_header, get_mft_index
from ntfs import get_non_resident_attribute, is_directory, get_sequence_number

from indx import get_index_records, is_slack, get_index_entry_parent_reference, get_index_entry_filename
from indx import get_index_entry_file_reference

from fmt import get_entry_output, get_format_header, warning, write_output_lines


class NoFilenameAttributeInRecordError(ValueError):
    pass


DESCRIPTION = r"""
   ___ _   _ ______  ______  _                       
  |_ _| \ | |  _ \ \/ /  _ \(_)_ __  _ __   ___ _ __ 
   | ||  \| | | | \  /| |_) | | '_ \| '_ \ / _ \ '__|
   | || |\  | |_| /  \|  _ <| | |_) | |_) |  __/ |   
  |___|_| \_|____/_/\_\_| \_\_| .__/| .__/ \___|_|   
                              |_|   |_|              
  carve file metadata from NTFS $I30 indexes

for more information, visit https://github.com/harelsegev/INDXRipper
"""


def get_arguments():
    parser = argparse.ArgumentParser(prog="INDXRipper",
                                     description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("image", help="image file to process")
    parser.add_argument("outfile", help="output file path")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")

    parser.add_argument("-m", "--mount-point", default=".", help="a string to prepend to file paths, such as \"C:\"")
    parser.add_argument("-o", "--offset", type=int, default=0, help="offset to an NTFS partition, in sectors")
    parser.add_argument("-b", "--sector-size", type=int, default=512, help="sector size in bytes. default is 512")
    parser.add_argument("-f", "--output-format",
                        choices=["csv", "jsonl", "bodyfile"], default="csv", help="output format. default is csv")

    parser.add_argument("--skip-deleted-dirs", action="store_true", help="don't search entries in deleted directories")

    parser.add_argument("--no-active-files",
                        action="store_true", help="filter out entries (both allocated and slack) of active files")

    parser.add_argument("--dedup", action="store_true", help="deduplicate output lines")
    return parser.parse_args()


def get_filename_parent_reference(filename_attribute):
    parent_reference = filename_attribute["ParentDirectoryReference"]
    return parent_reference["FileRecordNumber"], parent_reference["SequenceNumber"]


def get_filename_attribute(mft_chunk, attribute_header):
    return parse_filename_attribute(get_resident_attribute(mft_chunk, attribute_header))


def is_directory_index_allocation(attribute_header):
    return get_attribute_type(attribute_header) == "INDEX_ALLOCATION" and get_attribute_name(attribute_header) == "$I30"


def get_mft_dict_values(vbr, raw_image, mft_chunk, record_header, is_allocated):
    values = {"$FILE_NAME": [], "$INDEX_ALLOCATION": []}

    for attribute_header in get_attribute_headers(mft_chunk, record_header):
        if get_attribute_type(attribute_header) == "FILE_NAME":
            values["$FILE_NAME"].append(get_filename_attribute(mft_chunk, attribute_header))

        elif is_directory_index_allocation(attribute_header):
            with suppress(EmptyNonResidentAttributeError):
                index_attribute = get_non_resident_attribute(vbr, raw_image, mft_chunk, attribute_header, is_allocated)
                values["$INDEX_ALLOCATION"].append(index_attribute)

    return values


def apply_fixup(mft_chunk, record_header, vbr):
    if apply_record_fixup(mft_chunk, record_header, vbr):
        return True

    record_index = get_mft_index(record_header)
    warning(f"fixup validation failed for file record {record_index}. skipping this record")

    return False


def get_mft_records(mft_data_attribute, vbr):
    for mft_chunk in get_mft_chunks(vbr, mft_data_attribute):
        record_headers = get_record_headers(mft_chunk, vbr)

        for record_header in record_headers:
            if record_header:
                yield mft_chunk, record_header


def add_values_to_mft_dict(mft_dict, key, values):
    if key not in mft_dict:
        mft_dict[key] = values
    else:
        mft_dict[key]["$INDEX_ALLOCATION"] += values["$INDEX_ALLOCATION"]
        mft_dict[key]["$FILE_NAME"] += values["$FILE_NAME"]


def add_to_mft_dict(mft_dict, mft_chunk, record_header, skip_deleted_dirs, raw_image, vbr):
    if is_directory(record_header) and apply_fixup(mft_chunk, record_header, vbr):
        is_allocated = is_used(record_header)

        if is_allocated or not skip_deleted_dirs:
            values = get_mft_dict_values(vbr, raw_image, mft_chunk, record_header, is_allocated)

            if is_base_record(record_header):
                index = get_mft_index(record_header)
                sequence = get_sequence_number(record_header)

                add_values_to_mft_dict(mft_dict, (index, sequence), values)
            else:
                base_reference = get_base_record_reference(record_header)
                add_values_to_mft_dict(mft_dict, base_reference, values)


def add_to_extra_mft_data_attributes(mft_chunk, record_header, extra_mft_data_attributes, raw_image, vbr):
    if is_used(record_header) and apply_fixup(mft_chunk, record_header, vbr):
        attribute_headers = get_attribute_headers(mft_chunk, record_header)

        with suppress(StopIteration):
            data_attribute_header = next(get_attribute_header(attribute_headers, "DATA"))
            data_attribute = get_non_resident_attribute(vbr, raw_image, mft_chunk, data_attribute_header, True)

            extra_mft_data_attributes.append(data_attribute)


def populate_mft_dict(mft_dict, raw_image, mft_data_attribute, skip_deleted_dirs, vbr):
    extra_mft_data_attributes = []

    for mft_chunk, record_header in get_mft_records(mft_data_attribute, vbr):
        if get_base_record_reference(record_header) == (0, 1):
            add_to_extra_mft_data_attributes(mft_chunk, record_header, extra_mft_data_attributes, raw_image, vbr)
        else:
            add_to_mft_dict(mft_dict, mft_chunk, record_header, skip_deleted_dirs, raw_image, vbr)

    return extra_mft_data_attributes


def get_mft_dict_helper(mft_dict, raw_image, mft_data_attribute, skip_deleted_dirs, vbr):
    extra_mft_data_attributes = populate_mft_dict(mft_dict, raw_image, mft_data_attribute, skip_deleted_dirs, vbr)

    for extra_mft_data_attribute in extra_mft_data_attributes:
        get_mft_dict_helper(mft_dict, raw_image, extra_mft_data_attribute, skip_deleted_dirs, vbr)


def get_mft_dict(raw_image, first_mft_data_attribute, skip_deleted_dirs, vbr):
    mft_dict = {}
    get_mft_dict_helper(mft_dict, raw_image, first_mft_data_attribute, skip_deleted_dirs, vbr)

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
            parent_reference = get_filename_parent_reference(filename)
            path_cache[key] = get_path_helper(mft_dict, parent_reference) + "/" + filename["FilenameInUnicode"]

        except NoFilenameAttributeInRecordError:
            path_cache[key] = "/$Orphan"

    return path_cache[key]


def get_path(mft_dict, key, mount_point):
    return mount_point + get_path_helper(mft_dict, key)


def get_parent_path(first_entry, mft_dict, root_name):
    if not is_slack(first_entry):
        parent_key = get_index_entry_parent_reference(first_entry)
        return get_path(mft_dict, parent_key, root_name)

    return "<Unknown>"


def get_index_entries_in_record(index_record, parent_path):
    for index_entry in index_record:
        index_entry["ParentPath"] = parent_path
        yield index_entry


def get_index_entries_in_deleted_attribute(index_attribute, vbr, mft_dict, key, root_name):
    for index_record in get_index_records(index_attribute, key, vbr):
        with suppress(StopIteration):
            first_entry = next(index_record)
            parent_path = get_parent_path(first_entry, mft_dict, root_name)

            first_entry["ParentPath"] = parent_path
            yield first_entry

            yield from get_index_entries_in_record(index_record, parent_path)


def get_index_entries_in_allocated_attribute(index_attribute, vbr, mft_dict, key, root_name):
    parent_path = get_path(mft_dict, key, root_name)
    for index_record in get_index_records(index_attribute, key, vbr):
        yield from get_index_entries_in_record(index_record, parent_path)


def get_index_entries_in_attribute(index_attribute, vbr, mft_dict, key, root_name):
    if index_attribute.is_allocated:
        yield from get_index_entries_in_allocated_attribute(index_attribute, vbr, mft_dict, key, root_name)
    else:
        yield from get_index_entries_in_deleted_attribute(index_attribute, vbr, mft_dict, key, root_name)


def get_all_index_entries(index_attributes, vbr, mft_dict, key, root_name):
    for index_attribute in index_attributes:
        yield from get_index_entries_in_attribute(index_attribute, vbr, mft_dict, key, root_name)


def get_slack_entries_helper(index_attribute, allocated_entries, slack_entries, vbr, mft_dict, key, root_name):
    for entry in get_index_entries_in_attribute(index_attribute, vbr, mft_dict, key, root_name):
        if is_slack(entry):
            slack_entries.append(entry)
        else:
            filename = get_index_entry_filename(entry)
            allocated_entries[filename] = get_index_entry_file_reference(entry)


def filter_slack_entries(allocated_entries, slack_entries):
    for entry in slack_entries:
        filename = get_index_entry_filename(entry)

        if filename in allocated_entries:
            if not get_index_entry_file_reference(entry) == allocated_entries[filename]:
                yield entry

        else:
            yield entry


def get_slack_index_entries(index_attributes, vbr, mft_dict, key, root_name):
    allocated_entries, slack_entries = {}, []

    for index_attribute in index_attributes:
        if index_attribute.is_allocated:
            get_slack_entries_helper(index_attribute, allocated_entries, slack_entries, vbr, mft_dict, key, root_name)

        else:
            yield from get_index_entries_in_attribute(index_attribute, vbr, mft_dict, key, root_name)

    yield from filter_slack_entries(allocated_entries, slack_entries)


def get_index_entries(index_attributes, vbr, mft_dict, key, root_name, no_active_files):
    if no_active_files:
        yield from get_slack_index_entries(index_attributes, vbr, mft_dict, key, root_name)
    else:
        yield from get_all_index_entries(index_attributes, vbr, mft_dict, key, root_name)


def get_output_lines_helper(index_entries, output_format):
    for index_entry in index_entries:
        yield get_entry_output(index_entry, output_format)


def get_output_lines(mft_dict, vbr, root_name, no_active_files, output_format):
    yield get_format_header(output_format)

    for key, values in mft_dict.items():
        if index_attributes := values["$INDEX_ALLOCATION"]:
            index_entries = get_index_entries(index_attributes, vbr, mft_dict, key, root_name, no_active_files)
            yield from get_output_lines_helper(index_entries, output_format)


def main():
    args = get_arguments()
    with open(args.image, "rb") as raw_image:
        vbr = get_boot_sector(raw_image, args.offset * args.sector_size)
        first_mft_data_attribute = get_first_mft_data_attribute(vbr, raw_image)
        mft_dict = get_mft_dict(raw_image, first_mft_data_attribute, args.skip_deleted_dirs, vbr)

        output_lines = get_output_lines(mft_dict, vbr, args.mount_point, args.no_active_files, args.output_format)
        write_output_lines(output_lines, args.outfile, args.dedup, args.output_format)


if __name__ == "__main__":
    main()
