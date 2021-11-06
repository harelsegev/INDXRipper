"""
    Provides functions for working with $INDEX_ALLOCATION attributes
    Author: Harel Segev
    5/12/2021
"""
from contextlib import suppress
from datetime import datetime, timedelta
from io import BytesIO
import re

from construct import Struct, Const, Padding, Array, Seek, Optional, StopIf, FlagsEnum, Enum, PaddedString, Adapter
from construct import Int8ul, Int16ul, Int32ul, Int64ul
from construct import Check, CheckError, StreamError

from ntfs import FILE_REFERENCE, NUM_OF_FIXUP_BYTES


INDX_RECORD_HEADER = Struct(
    "Magic" / Optional(Const(b'INDX')),
    StopIf(lambda this: this.Magic is None),

    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,
    Seek(lambda this: this.UpdateSequenceOffset),

    "UpdateSequence" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)


class FiletimeAdapter(Adapter):
    def _decode(self, obj, context, path):
        return datetime(1601, 1, 1) + timedelta(microseconds=(obj / 10))

    def _encode(self, obj, context, path):
        delta = obj - datetime(1601, 1, 1)
        return 10 ** 7 * delta.total_seconds()


Filetime = FiletimeAdapter(Int64ul)


INDEX_ENTRY = Struct(
    "FILE_REFERENCE" / FILE_REFERENCE,
    Padding(16),

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


def get_indx_records(raw_indx_nonresident_stream, vbr):
    while current_record := raw_indx_nonresident_stream.read(vbr["BytsPerIndx"]):
        yield current_record


def get_indx_record_header(indx_record):
    return INDX_RECORD_HEADER.parse(indx_record)


def is_valid_indx_record(record_header):
    return record_header["Magic"] is not None


def apply_fixup(indx_record, record_header, vbr):
    first_fixup_offset = vbr["BytsPerSec"] - NUM_OF_FIXUP_BYTES
    for i, usn_offset in enumerate(range(first_fixup_offset, vbr["BytsPerIndx"], vbr["BytsPerSec"])):
        indx_record[usn_offset:usn_offset + NUM_OF_FIXUP_BYTES] = Int16ul.build(record_header["UpdateSequenceArray"][i])


def find_parent_reference_offsets(indx_record, parent_index, parent_sequence):
    parent_reference = FILE_REFERENCE.build({'FileRecordNumber': parent_index, 'SequenceNumber': parent_sequence})
    return [match.start() for match in re.finditer(re.escape(parent_reference), indx_record)]


FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY = 16


def get_index_entries(indx_record, parent_index, parent_sequence):
    indx_record_stream = BytesIO(indx_record)

    for offset in find_parent_reference_offsets(indx_record, parent_index, parent_sequence):
        entry_offset = offset - FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY

        if entry_offset >= 0:
            indx_record_stream.seek(entry_offset)

            with suppress(StreamError, OverflowError, CheckError, UnicodeDecodeError):
                yield INDEX_ENTRY.parse_stream(indx_record_stream)


def find_index_entries(raw_index_nonresident_stream, parent_index, parent_sequence, vbr):
    for indx_record in get_indx_records(raw_index_nonresident_stream, vbr):
        record_header = get_indx_record_header(indx_record)

        if is_valid_indx_record(record_header):
            apply_fixup(indx_record, record_header, vbr)

            for entry in get_index_entries(indx_record, parent_index, parent_sequence):
                yield entry
