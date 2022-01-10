"""
    Provides functions for working with $INDEX_ALLOCATION attributes
    Author: Harel Segev
    5/12/2021
"""

from construct import Struct, Const, Padding, Array, Seek, Optional, StopIf, FlagsEnum, Enum
from construct import PaddedString, Int8ul, Int16ul, Int32ul, Int64ul
from construct import Check, CheckError, StreamError

from contextlib import suppress
from io import BytesIO
import re

from ntfs import FILE_REFERENCE

INDEX_RECORD_HEADER = Struct(
    "Magic" / Optional(Const(b'INDX')),
    StopIf(lambda this: this.Magic is None),

    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,

    Padding(20),
    "EndOfEntriesOffset" / Int32ul,

    Seek(lambda this: this.UpdateSequenceOffset),
    "UpdateSequence" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)


INDEX_ENTRY = Struct(
    "FILE_REFERENCE" / FILE_REFERENCE,
    "IndexEntrySize" / Int16ul,
    Padding(14),

    "CreationTime" / Int64ul,
    "LastModificationTime" / Int64ul,
    "LastMftChangeTime" / Int64ul,
    "LastAccessTime" / Int64ul,

    "AllocatedSize" / Int64ul,
    "RealSize" / Int64ul,
    "Flags" / FlagsEnum(Int32ul,
                        READ_ONLY=0x0001,
                        HIDDEN=0x0002,
                        SYSTEM=0x0004,
                        ARCHIVE=0x0020,
                        DEVICE=0x0040,
                        NORMAL=0x0080,
                        TEMPORARY=0x0100,
                        SPARSE=0x0200,
                        REPARSE_POINT=0x0400,
                        COMPRESSED=0x0800,
                        OFFLINE=0x1000,
                        NOT_CONTENT_INDEXED=0x2000,
                        ENCRYPTED=0x4000,
                        DIRECTORY=0x10000000,
                        INDEX_VIEW=0x20000000),
    Padding(4),

    "FilenameLengthInCharacters" / Int8ul,
    Check(lambda this: this.FilenameLengthInCharacters != 0),

    "FilenameNamespace" / Enum(Int8ul, POSIX=0, WIN32=1, DOS=2, WIN32_DOS=3),
    "FilenameInUnicode" / PaddedString(lambda this: this.FilenameLengthInCharacters * 2, "utf16")
)


def get_index_records_helper(index_allocation_attribute, vbr):
    while current_record := index_allocation_attribute.read(vbr["BytsPerIndx"]):
        yield current_record


def get_index_record_header(index_record):
    return INDEX_RECORD_HEADER.parse(index_record)


def is_valid_index_record(record_header):
    return record_header["Magic"] is not None


NODE_HEADER_OFFSET_IN_RECORD = 24


def get_slack_offset(record_header):
    return record_header["EndOfEntriesOffset"] + NODE_HEADER_OFFSET_IN_RECORD


def apply_fixup(index_record, record_header, vbr):
    for i, usn_offset in enumerate(range(vbr["BytsPerSec"] - 2, vbr["BytsPerIndx"], vbr["BytsPerSec"])):
        index_record[usn_offset:usn_offset + 2] = Int16ul.build(record_header["UpdateSequenceArray"][i])


def get_index_records(index_allocation_attribute, vbr):
    for index_record in get_index_records_helper(index_allocation_attribute, vbr):
        record_header = get_index_record_header(index_record)

        if is_valid_index_record(record_header):
            apply_fixup(index_record, record_header, vbr)

            yield index_record, record_header


def get_parent_reference_offsets(index_record, parent_reference):
    parent_index, parent_sequence = parent_reference

    magic = FILE_REFERENCE.build({
        "FileRecordNumber": parent_index,
        "SequenceNumber": parent_sequence
    })

    return [match.start() for match in re.finditer(re.escape(magic), index_record)]


FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY = 16


def get_entries_in_record(index_record, parent_reference):
    index_record_stream = BytesIO(index_record)

    for offset in get_parent_reference_offsets(index_record, parent_reference):
        entry_offset = offset - FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY

        if entry_offset >= 0:
            index_record_stream.seek(entry_offset)

            with suppress(StreamError, CheckError, OverflowError, UnicodeDecodeError):
                yield INDEX_ENTRY.parse_stream(index_record_stream), entry_offset


def get_entry_size(index_entry):
    return index_entry["IndexEntrySize"]


def get_all_entries(index_allocation_attributes, parent_reference, vbr):
    for index_allocation_attribute in index_allocation_attributes:
        for index_record, record_header in get_index_records(index_allocation_attribute, vbr):
            slack_offset = get_slack_offset(record_header)

            for entry, entry_offset in get_entries_in_record(index_record, parent_reference):
                entry["IsSlack"] = entry_offset + get_entry_size(entry) >= slack_offset
                yield entry


def get_slack_entries_helper(index_allocation_attributes, parent_reference, vbr):
    allocated_entries, slack_entries = {}, []

    for entry in get_all_entries(index_allocation_attributes, parent_reference, vbr):
        if entry["IsSlack"]:
            slack_entries.append(entry)
        else:
            entry_filename = entry["FilenameInUnicode"]
            allocated_entries[entry_filename] = get_file_reference(entry)

    return allocated_entries, slack_entries


def get_file_reference(entry):
    return entry["FILE_REFERENCE"]["FileRecordNumber"], entry["FILE_REFERENCE"]["SequenceNumber"]


def get_slack_entries(index_allocation_attributes, parent_reference, vbr):
    allocated_entries, slack_entries = get_slack_entries_helper(
        index_allocation_attributes,
        parent_reference,
        vbr
    )

    for entry in slack_entries:
        filename = entry["FilenameInUnicode"]

        if filename in allocated_entries:
            if not get_file_reference(entry) == allocated_entries[filename]:
                yield entry

        else:
            yield entry


def get_entries(index_allocation_attributes, parent_reference, slack_only, vbr):
    if slack_only:
        return get_slack_entries(index_allocation_attributes, parent_reference, vbr)
    else:
        return get_all_entries(index_allocation_attributes, parent_reference, vbr)
