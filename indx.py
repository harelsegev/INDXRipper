"""
    Provides functions for working with $INDEX_ALLOCATION attributes
    Author: Harel Segev
    5/12/2021
"""
from io import BytesIO

from construct import Struct, Int16ul, Const, Padding, Array, Int32ul, ConstError
from ntfs import FILE_REFERENCE, FILENAME_ATTRIBUTE, NUM_OF_FIXUP_BYTES
import re

NODE_HEADER_OFFSET = 24

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


def _get_index_records(raw_index_nonresident_stream, vbr):
    while current_record := raw_index_nonresident_stream.read(vbr["BytsPerIndx"]):
        yield current_record


def _get_index_record_header(index_record):
    return INDEX_RECORD_HEADER.parse(index_record)


def _get_node_slack_offset(record_header):
    return NODE_HEADER_OFFSET + record_header["FirstEntryOffset"] + record_header["TotalSizeOfIndexEntries"]


def _apply_fixup(index_record, record_header, vbr):
    for i, usn_offset in enumerate(range(vbr["BytsPerSec"] - NUM_OF_FIXUP_BYTES, vbr["BytsPerIndx"], vbr["BytsPerSec"])):
        index_record[usn_offset:usn_offset + NUM_OF_FIXUP_BYTES] = Int16ul.build(record_header["UpdateSequenceArray"][i])


def _find_parent_reference_offsets(index_record, parent_mft_record_index, parent_mft_record_sequence):
    parent_reference = FILE_REFERENCE.build({'FileRecordNumber': parent_mft_record_index,
                                             'SequenceNumber': parent_mft_record_sequence})
    return (match.start() for match in re.finditer(re.escape(parent_reference), index_record))


def _find_index_entries(index_record, parent_mft_record_index, parent_mft_record_sequence, start):
    parent_reference_offsets = _find_parent_reference_offsets(index_record, parent_mft_record_index,
                                                              parent_mft_record_sequence)

    index_record = BytesIO(index_record)
    for offset in parent_reference_offsets:
        if offset >= start:
            index_record.seek(offset)
            yield FILENAME_ATTRIBUTE.parse_stream(index_record)


def find_index_entries(raw_index_nonresident_stream, parent_mft_record_index, parent_mft_record_sequence, vbr, slack_only):
    for index_record in _get_index_records(raw_index_nonresident_stream, vbr):
        try:
            record_header = _get_index_record_header(index_record)
        except ConstError:
            continue

        _apply_fixup(index_record, record_header, vbr)

        start = 0
        if slack_only:
            start = _get_node_slack_offset(record_header)

        for entry in _find_index_entries(index_record, parent_mft_record_index, parent_mft_record_sequence, start):
            yield entry
