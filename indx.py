"""
    Provides functions for working with $INDEX_ALLOCATION attributes
    Author: Harel Segev
    5/12/2021
"""

from construct import Struct, Int16ul, Const, Padding, Array, Int32ul, ConstError, Seek
from ntfs import FILE_REFERENCE, FILENAME_ATTRIBUTE, NUM_OF_FIXUP_BYTES
import re

FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY = 16

INDEX_RECORD_HEADER = Struct(
    "Magic" / Const(b'INDX'),
    Padding(2),
    "UpdateSequenceSize" / Int16ul,
    Padding(16),
    "FirstEntryOffset" / Int32ul,
    "TotalSizeOfIndexEntries" / Int32ul,
    "AllocatedSizeOfNode" / Int32ul,
    Padding(4),
    "UpdateSequence" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)

INDEX_ENTRY = Struct(
    Seek(lambda this: this._.offset),
    "FILE_REFERENCE" / FILE_REFERENCE,
    Padding(8),
    "FILENAME_ATTRIBUTE" / FILENAME_ATTRIBUTE
)


def get_indx_records(raw_indx_nonresident_stream, vbr):
    while current_record := raw_indx_nonresident_stream.read(vbr["BytsPerIndx"]):
        yield current_record


def get_indx_record_header(index_record):
    return INDEX_RECORD_HEADER.parse(index_record)


def apply_fixup(indx_record, record_header, vbr):
    first_fixup_offset = vbr["BytsPerSec"] - NUM_OF_FIXUP_BYTES
    for i, usn_offset in enumerate(range(first_fixup_offset, vbr["BytsPerIndx"], vbr["BytsPerSec"])):
        indx_record[usn_offset:usn_offset + NUM_OF_FIXUP_BYTES] = Int16ul.build(record_header["UpdateSequenceArray"][i])


def find_parent_reference_offsets(indx_record, parent_index, parent_sequence):
    parent_reference = FILE_REFERENCE.build({'FileRecordNumber': parent_index, 'SequenceNumber': parent_sequence})
    return (match.start() for match in re.finditer(re.escape(parent_reference), indx_record))


def get_indx_entries(indx_record, parent_index, parent_sequence):
    parent_reference_offsets = find_parent_reference_offsets(indx_record, parent_index, parent_sequence)
    for offset in parent_reference_offsets:
        entry_offset = offset - FILENAME_ATTRIBUTE_OFFSET_IN_ENTRY
        if entry_offset >= 0:
            yield INDEX_ENTRY.parse(indx_record, offset=entry_offset)


def find_index_entries(raw_index_nonresident_stream, parent_index, parent_sequence, vbr):
    for index_record in get_indx_records(raw_index_nonresident_stream, vbr):
        try:
            record_header = get_indx_record_header(index_record)
        except ConstError:
            continue

        apply_fixup(index_record, record_header, vbr)
        for entry in get_indx_entries(index_record, parent_index, parent_sequence):
            yield entry
