"""
    Carve file metadata from NTFS $I30 indexes
    Author: Harel Segev
    05/16/2021
"""
__version__ = "5.2.2"

import os
import random
import argparse
from contextlib import suppress
from string import ascii_uppercase, ascii_lowercase

from ntfs import parse_filename_attribute, get_resident_attribute, get_attribute_name, get_attribute_type
from ntfs import get_boot_sector, get_mft_data_attribute, get_base_record_reference, is_base_record
from ntfs import is_valid_fixup, is_valid_record_signature, get_attribute_headers, is_used
from ntfs import EmptyNonResidentAttributeError, get_non_resident_attribute, is_directory
from ntfs import get_mft_chunks, get_record_headers, apply_fixup, get_sequence_number

from indx import get_index_records, is_slack, get_index_entry_parent_reference, get_index_entry_filename
from indx import get_index_entry_file_reference

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
            if is_allocated := is_used(record_header) or deleted_dirs:
                values = get_mft_dict_values(vbr, raw_image, mft_chunk, record_header, is_allocated)

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
            parent_reference = get_filename_parent_reference(filename)
            path_cache[key] = get_path_helper(mft_dict, parent_reference) + "/" + filename["FilenameInUnicode"]

        except NoFilenameAttributeInRecordError:
            path_cache[key] = "/$Orphan"

    return path_cache[key]


def get_path(mft_dict, key, mount_point):
    return mount_point + get_path_helper(mft_dict, key)


def get_parent_path(first_entry, mft_dict, key, root_name, is_allocated):
    if not is_slack(first_entry):
        parent_key = get_index_entry_parent_reference(first_entry)
        return get_path(mft_dict, parent_key, root_name)

    elif is_allocated:
        return get_path(mft_dict, key, root_name)

    return "<Unknown>"


def get_index_entries_in_attribute_helper(first_entry, index_record, parent_path):
    first_entry["ParentPath"] = parent_path
    yield first_entry

    for entry in index_record:
        entry["ParentPath"] = parent_path
        yield entry


def get_index_entries_in_attribute(index_attribute, vbr, mft_dict, key, root_name):
    is_allocated = index_attribute.is_allocated

    for index_record in get_index_records(index_attribute, vbr):
        with suppress(StopIteration):
            first_entry = next(index_record)
            parent_path = get_parent_path(first_entry, mft_dict, key, root_name, is_allocated)

            yield from get_index_entries_in_attribute_helper(first_entry, index_record, parent_path)


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
        filename = entry["FilenameInUnicode"]

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


def get_index_entries(index_attributes, vbr, mft_dict, key, root_name, slack_only):
    if slack_only:
        yield from get_slack_index_entries(index_attributes, vbr, mft_dict, key, root_name)
    else:
        yield from get_all_index_entries(index_attributes, vbr, mft_dict, key, root_name)


def get_output_lines_helper(index_entries, output_format):
    for index_entry in index_entries:
        yield get_entry_output(index_entry, output_format)


def get_output_lines(mft_dict, vbr, root_name, slack_only, output_format):
    yield get_format_header(output_format)

    for key in mft_dict:
        if index_attributes := mft_dict[key]["$INDEX_ALLOCATION"]:
            index_entries = get_index_entries(index_attributes, vbr, mft_dict, key, root_name, slack_only)
            yield from get_output_lines_helper(index_entries, output_format)


def dedup(infile_path):
    with open(infile_path, "rt", encoding="utf-8") as infile:
        outfile_name = "".join(random.choices(ascii_uppercase + ascii_lowercase, k=6))
        outfile_path = os.path.join(os.path.dirname(infile_path), outfile_name)

        with open(outfile_path, "wt+", encoding="utf-8") as outfile:
            with suppress(StopIteration):
                outfile.write(next(infile))
                outfile.writelines(set(infile))

    os.remove(infile_path)
    os.rename(outfile_path, infile_path)


def main():
    args = get_arguments()
    with open(args.image, "rb") as raw_image:
        vbr = get_boot_sector(raw_image, args.o * args.b)
        mft_data = get_mft_data_attribute(vbr, raw_image)
        mft_dict = get_mft_dict(raw_image, mft_data, args.deleted_dirs, vbr)

        with open(args.outfile, "at+", encoding="utf-8") as outfile:
            for lines in get_output_lines(mft_dict, vbr, args.m, args.slack_only, args.w):
                outfile.writelines(lines)

    if args.dedup:
        dedup(args.outfile)


if __name__ == '__main__':
    main()
