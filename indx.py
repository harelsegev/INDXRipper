"""
    Provides functions for working with $INDEX_ALLOCATION attributes
    Author: Harel Segev
    5/12/2021
"""

from construct import Struct, Const, Padding, Array, Seek, Optional, StopIf, FlagsEnum, Enum, Check
from construct import PaddedString, Adapter, CheckError, Computed, Int8ul, Int16ul, Int32ul, Int64ul
from construct import StreamError

from datetime import datetime, timedelta
from contextlib import suppress
from io import BytesIO
import itertools
import re

from ntfs import FILE_REFERENCE
from fmt import warning

INDEX_RECORD_HEADER = Struct(
    "Magic" / Optional(Const(b'INDX')),
    StopIf(lambda this: this.Magic is None),

    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,

    Padding(16),
    "FirstEntryOffset" / Int32ul,
    "EndOfEntriesOffset" / Int32ul,

    Seek(lambda this: this.UpdateSequenceOffset),
    "UpdateSequence" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)


class FiletimeAdapter(Adapter):
    def _decode(self, obj, context, path):
        return datetime(1601, 1, 1) + timedelta(microseconds=(obj / 10))


Filetime = FiletimeAdapter(Int64ul)


INDEX_ENTRY = Struct(
    "FILE_REFERENCE" / FILE_REFERENCE,
    "EntrySize" / Int16ul,
    Padding(2),

    "EntryFlags" / FlagsEnum(Int8ul, POINTS_TO_A_SUBNODE=0x01, LAST_ENTRY=0x2),
    StopIf(lambda this: this.EntryFlags["LAST_ENTRY"] and not this._.is_slack),

    Padding(3),
    "PARENT_REFERENCE" / FILE_REFERENCE,
    "CreationTime" / Filetime,
    "LastModificationTime" / Filetime,
    "LastMftChangeTime" / Filetime,
    "LastAccessTime" / Filetime,

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

    "FilenameNamespace" / Enum(Int8ul, POSIX=0, WIN32=1, DOS=2, WIN32_DOS=3),
    "FilenameInUnicode" / PaddedString(lambda this: this.FilenameLengthInCharacters * 2, "utf16"),
    Check(lambda this: not this._.is_slack or this.FilenameInUnicode.isprintable()),

    "IsSlack" / Computed(lambda this: this._.is_slack)
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


def get_first_entry_offset(record_header):
    return record_header["FirstEntryOffset"] + NODE_HEADER_OFFSET_IN_RECORD


def apply_fixup(index_record, record_header, vbr):
    for i, usn_offset in enumerate(range(vbr["BytsPerSec"] - 2, vbr["BytsPerIndx"], vbr["BytsPerSec"])):
        if Int16ul.parse(index_record[usn_offset:usn_offset + 2]) != record_header["UpdateSequence"]:
            return False

        index_record[usn_offset:usn_offset + 2] = Int16ul.build(record_header["UpdateSequenceArray"][i])

    return True


def get_index_records(index_allocation_attribute, vbr):
    for index_record in get_index_records_helper(index_allocation_attribute, vbr):
        record_header = get_index_record_header(index_record)

        if is_valid_index_record(record_header):
            record_header["IsValidFixup"] = apply_fixup(index_record, record_header, vbr)
            yield index_record, record_header


def is_valid_fixup(record_header):
    return record_header["IsValidFixup"]


def get_allocated_entries_in_record(index_record, record_header):
    index_record_stream = BytesIO(index_record)
    current_offset = get_first_entry_offset(record_header)

    while True:
        index_record_stream.seek(current_offset)
        current_entry = INDEX_ENTRY.parse_stream(index_record_stream, is_slack=False)
        if current_entry["EntryFlags"]["LAST_ENTRY"]:
            break

        yield current_entry
        current_offset += current_entry["EntrySize"]


TIMESTAMPS_OFFSET_IN_ENTRY = 24

# TODO: Change me in 2026
CARVER_QUERY = re.compile(
    b"(?=("
    
    # 4 Timestamps: Sat 11 January 1997 20:42:45 UTC - Fri 19 June 2026 15:26:29 UTC
    b"([\x00-\xFF]{6}[\xBC-\xDC]\x01){4}"
    
    # Allocated size: divisible by 8
    b"[\x00\x08\x10\x18\x20\x28\x30\x38\x40\x48\x50\x58\x60\x68\x70\x78"
    b"\x80\x88\x90\x98\xA0\xA8\xB0\xB8\xC0\xC8\xD0\xD8\xE0\xE8\xF0\xF8]"
    
    # Padding
    b"[\x00-\xFF]{23}"
    
    # Name length: != 0
    b"[^\x00]"
    
    # Namespace: 0 - 3
    b"[\x00-\x03]"
    
    b"))"
)


def get_slack_entry_offsets(index_slack):
    for match in re.finditer(CARVER_QUERY, index_slack):
        entry_offset = match.start() - TIMESTAMPS_OFFSET_IN_ENTRY

        if entry_offset >= 0:
            yield entry_offset


def get_slack_entries_in_record(index_slack, record_belongs_to_parent):
    index_slack_stream = BytesIO(index_slack)

    for entry_offset in get_slack_entry_offsets(index_slack):
        index_slack_stream.seek(entry_offset)

        with suppress(StreamError, CheckError, OverflowError, UnicodeDecodeError):
            entry = INDEX_ENTRY.parse_stream(index_slack_stream, is_slack=True)

            entry["BelongsToParent"] = record_belongs_to_parent
            yield entry


def get_parent_reference_as_key(index_entry):
    return index_entry["PARENT_REFERENCE"]["FileRecordNumber"], index_entry["PARENT_REFERENCE"]["SequenceNumber"]


def remove_allocated_space(index_record, record_header):
    del index_record[:get_slack_offset(record_header)]
    index_record[:0] = b"\x00" * TIMESTAMPS_OFFSET_IN_ENTRY


def get_all_entries_in_record(index_record, record_header, parent_reference, is_allocated):
    allocated_entries = get_allocated_entries_in_record(index_record, record_header)
    record_belongs_to_parent = True

    try:
        first_entry = next(allocated_entries)
        record_belongs_to_parent = get_parent_reference_as_key(first_entry) == parent_reference

        for entry in itertools.chain([first_entry], allocated_entries):
            entry["BelongsToParent"] = record_belongs_to_parent
            yield entry

    except StopIteration:
        if not is_allocated:
            # this index record might belong to the directory, but we cannot say for sure.
            record_belongs_to_parent = False

    remove_allocated_space(index_record, record_header)
    yield from get_slack_entries_in_record(index_record, record_belongs_to_parent)


def get_all_entries_in_attribute(index_allocation_attribute, parent_reference, vbr):
    is_allocated = index_allocation_attribute.is_allocated

    for index_record, record_header in get_index_records(index_allocation_attribute, vbr):
        if is_valid_fixup(record_header):
            yield from get_all_entries_in_record(index_record, record_header, parent_reference, is_allocated)
        else:
            warning("fixup validation failed for an index record. the entire record will be treated as slack space")
            yield from get_slack_entries_in_record(index_record, False)


def get_all_entries(index_allocation_attributes, parent_reference, vbr):
    for index_allocation_attribute in index_allocation_attributes:
        yield from get_all_entries_in_attribute(index_allocation_attribute, parent_reference, vbr)


def get_slack_entries_helper(index_allocation_attribute, allocated_entries, slack_entries, parent_reference, vbr):
    for entry in get_all_entries_in_attribute(index_allocation_attribute, parent_reference, vbr):
        if entry["IsSlack"]:
            slack_entries.append(entry)
        else:
            entry_filename = entry["FilenameInUnicode"]
            allocated_entries[entry_filename] = get_file_reference(entry)


def get_file_reference(entry):
    return entry["FILE_REFERENCE"]["FileRecordNumber"], entry["FILE_REFERENCE"]["SequenceNumber"]


def filter_slack_entries(allocated_entries, slack_entries):
    for entry in slack_entries:
        filename = entry["FilenameInUnicode"]

        if filename in allocated_entries:
            if not get_file_reference(entry) == allocated_entries[filename]:
                yield entry

        else:
            yield entry


def get_slack_entries(index_allocation_attributes, parent_reference, vbr):
    alloc_entries, slack_entries = {}, []

    for index_allocation_attribute in index_allocation_attributes:
        if index_allocation_attribute.is_allocated:
            get_slack_entries_helper(index_allocation_attribute, alloc_entries, slack_entries, parent_reference, vbr)

        else:
            yield from get_all_entries_in_attribute(index_allocation_attribute, parent_reference, vbr)

    yield from filter_slack_entries(alloc_entries, slack_entries)


def get_entries(index_allocation_attributes, parent_reference, slack_only, vbr):
    if slack_only:
        return get_slack_entries(index_allocation_attributes, parent_reference, vbr)
    else:
        return get_all_entries(index_allocation_attributes, parent_reference, vbr)
