"""
    Provides functions for working with NTFS volumes
    Author: Harel Segev
    05/16/2020
"""

from construct import Struct, Padding, Computed, IfThenElse, BytesInteger, Const, Enum, Array, FlagsEnum, Switch, Tell
from construct import PaddedString, Pointer, Seek, Optional, StopIf, RepeatUntil, Padded
from construct import Int8ul, Int16ul, Int32ul, Int64ul, Int8sl

from dataruns import get_dataruns, NonResidentStream
from sys import exit as sys_exit


class EmptyNonResidentAttributeError(ValueError):
    pass


BOOT_SECTOR = Struct(
    "OffsetInImage" / Tell,
    Padding(11),
    "BytsPerSec" / Int16ul,
    "SecPerClus" / Int8ul,
    "BytsPerClus" / Computed(lambda this: this.BytsPerSec * this.SecPerClus),

    Padding(34),
    "MftClusNumber" / Int64ul,
    Padding(8),

    "BytsOrClusPerRec" / Int8sl,
    "BytsPerRec" / IfThenElse(
        lambda this: this.BytsOrClusPerRec > 0,
        Computed(lambda this: this.BytsOrClusPerRec * this.BytsPerClus),
        Computed(lambda this: 2 ** abs(this.BytsOrClusPerRec)),
    ),
    Padding(3),

    "BytsOrClusPerIndx" / Int8sl,
    "BytsPerIndx" / IfThenElse(
        lambda this: this.BytsOrClusPerIndx > 0,
        Computed(lambda this: this.BytsOrClusPerIndx * this.BytsPerClus),
        Computed(lambda this: 2 ** abs(this.BytsOrClusPerIndx)),
    ),

    "BytsPerMftChunk" / IfThenElse(
        lambda this: this.BytsPerClus > this.BytsPerRec,
        Computed(lambda this: this.BytsPerClus),
        Computed(lambda this: this.BytsPerRec)
    ),
)

FILE_REFERENCE = Struct(
    "FileRecordNumber" / BytesInteger(6, swapped=True, signed=False),
    "SequenceNumber" / Int16ul
)

FILE_RECORD_HEADER = Struct(
    "OffsetInChunk" / Tell,
    "Magic" / Optional(Const(b'FILE')),
    StopIf(lambda this: this.Magic is None),

    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,

    Padding(8),
    "SequenceNumber" / Int16ul,
    Padding(2),
    "FirstAttributeOffset" / Int16ul,
    "Flags" / FlagsEnum(Int16ul, IN_USE=1, DIRECTORY=2),
    Padding(8),
    "BaseRecordReference" / FILE_REFERENCE,
    "NextAttributeId" / Int16ul,

    Seek(lambda this: this.UpdateSequenceOffset + this.OffsetInChunk),
    "UpdateSequenceNumber" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)

FILE_RECORD_HEADERS = Struct(
    "RecordHeaders" / Array(
        lambda this: this._.records_per_chunk,
        Padded(lambda this: this._.bytes_per_record, FILE_RECORD_HEADER)
    )
)

ATTRIBUTE_HEADER = Struct(
    "EndOfRecordSignature" / Optional(Const(b'\xFF\xFF\xFF\xFF')),
    StopIf(lambda this: this.EndOfRecordSignature is not None),

    "OffsetInChunk" / Tell,
    "Type" / Enum(Int32ul, FILE_NAME=0x30, INDEX_ALLOCATION=0xA0, DATA=0x80),
    "Length" / Int32ul,
    "Residence" / Enum(Int8ul, RESIDENT=0x00, NON_RESIDENT=0x01),
    "NameLength" / Int8ul,
    "NameOffset" / Int16ul,
    "AttributeName" / Pointer(lambda this: this.NameOffset + this.OffsetInChunk,
                              PaddedString(lambda this: 2 * this.NameLength, "utf16")),
    Padding(2),
    "AttributeId" / Int16ul,
    "Metadata" / Switch(
        lambda this: this.Residence,
        {
            "RESIDENT":
                Struct(
                    "AttributeLength" / Int32ul,
                    "AttributeOffset" / Int16ul,
                ),
            "NON_RESIDENT":
                Struct(
                    Padding(16),
                    "DataRunsOffset" / Int16ul,
                    Padding(6),
                    "AllocatedSize" / Int64ul,
                    "RealSize" / Int64ul,
                )
        }
    ),

    Seek(lambda this: this.Length + this.OffsetInChunk)
)

ATTRIBUTE_HEADERS = Struct(
    Seek(lambda this: this._.offset),
    "AttributeHeaders" / RepeatUntil(lambda obj, lst, ctx: obj.EndOfRecordSignature is not None, ATTRIBUTE_HEADER)
)

FILENAME_ATTRIBUTE = Struct(
    "ParentDirectoryReference" / FILE_REFERENCE,
    "CreationTime" / Int64ul,
    "LastModificationTime" / Int64ul,
    "LastMftChangeTime" / Int64ul,
    "LastAccessTime" / Int64ul,
    "AllocatedSize" / Int64ul,
    "RealSize" / Int64ul,
    "Flags" / Enum(Int32ul,
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
    "FilenameNamespace" / Enum(Int8ul,
                               POSIX=0,
                               WIN32=1,
                               DOS=2,
                               WIN32_DOS=3),
    "FilenameInUnicode" / PaddedString(lambda this: this.FilenameLengthInCharacters * 2, "utf16")
)


def get_boot_sector(raw_image, partition_offset):
    raw_image.seek(partition_offset)
    return BOOT_SECTOR.parse_stream(raw_image)


def get_mft_offset(vbr):
    return vbr["MftClusNumber"] * vbr["BytsPerClus"] + vbr["OffsetInImage"]


def get_first_mft_chunk(vbr, raw_image):
    raw_image.seek(get_mft_offset(vbr))
    return bytearray(raw_image.read(vbr["BytsPerMftChunk"]))


NUM_OF_FIXUP_BYTES = 2


def get_record_headers(mft_chunk, vbr):
    return FILE_RECORD_HEADERS.parse(
        mft_chunk,
        bytes_per_record=vbr["BytsPerRec"],
        records_per_chunk=vbr["BytsPerMftChunk"] // vbr["BytsPerRec"]
    )["RecordHeaders"]


def is_valid_record_signature(record_header):
    return record_header["Magic"] is not None


def apply_record_fixup(mft_chunk, record_header, vbr):
    usn = record_header["UpdateSequenceNumber"]
    first_fixup_offset = record_header["OffsetInChunk"] + vbr["BytsPerSec"] - NUM_OF_FIXUP_BYTES
    end_of_record_offset = record_header["OffsetInChunk"] + vbr["BytsPerRec"]

    for i, usn_offset in enumerate(range(first_fixup_offset, end_of_record_offset, vbr["BytsPerSec"])):
        if Int16ul.parse(mft_chunk[usn_offset:usn_offset + NUM_OF_FIXUP_BYTES]) != usn:
            return False

        mft_chunk[usn_offset:usn_offset + NUM_OF_FIXUP_BYTES] = Int16ul.build(record_header["UpdateSequenceArray"][i])

    return True


def apply_fixup(mft_chunk, record_headers, vbr):
    for record_header in record_headers:
        if is_valid_record_signature(record_header):
            record_header["IsValidFixup"] = apply_record_fixup(mft_chunk, record_header, vbr)


def is_valid_fixup(record_header):
    return record_header["IsValidFixup"]


def is_directory(record_header):
    return record_header["Flags"]["DIRECTORY"]


def get_sequence_number(record_header):
    return record_header["SequenceNumber"]


def is_base_record(record_header):
    return record_header["BaseRecordReference"]["FileRecordNumber"] == 0


def get_base_record_reference(record_header):
    base_reference = record_header["BaseRecordReference"]
    return base_reference["FileRecordNumber"], base_reference["SequenceNumber"]


def get_attribute_headers(mft_chunk, record_header):
    first_attribute_offset = record_header["FirstAttributeOffset"] + record_header["OffsetInChunk"]
    res = ATTRIBUTE_HEADERS.parse(mft_chunk, offset=first_attribute_offset)
    return res["AttributeHeaders"][:-1]


def get_resident_attribute(mft_chunk, attribute_header):
    offset = attribute_header["OffsetInChunk"] + attribute_header["Metadata"]["AttributeOffset"]
    return mft_chunk[offset: offset + attribute_header["Metadata"]["AttributeLength"]]


def get_attribute_type(attribute_header):
    return attribute_header["Type"]


def get_attribute_name(attribute_header):
    return attribute_header["AttributeName"]


def is_resident(attribute_header):
    return attribute_header["Residence"]["RESIDENT"]


def get_attribute_header(attribute_headers, attribute_type):
    for attribute_header in attribute_headers:
        if attribute_header["Type"] == attribute_type:
            yield attribute_header


def parse_filename_attribute(filename_attribute):
    return FILENAME_ATTRIBUTE.parse(filename_attribute)


def get_non_resident_attribute(vbr, raw_image, mft_chunk, attribute_header):
    if attribute_header["Metadata"]["AllocatedSize"] == 0 or attribute_header["Metadata"]["RealSize"] == 0:
        raise EmptyNonResidentAttributeError

    dataruns_offset_in_chunk = attribute_header["OffsetInChunk"] + attribute_header["Metadata"]["DataRunsOffset"]
    dataruns = get_dataruns(mft_chunk, dataruns_offset_in_chunk)
    return NonResidentStream(vbr["BytsPerClus"], vbr["OffsetInImage"], raw_image, dataruns)


def is_first_record_valid(record_header):
    if not is_valid_record_signature(record_header):
        sys_exit(f"INDXRipper: error: invalid 'FILE' signature in first file record")

    if not is_valid_fixup(record_header):
        sys_exit(f"INDXRipper: error: fixup verification failed for first file record")

    return True


def get_mft_data_attribute(vbr, raw_image):
    mft_chunk = get_first_mft_chunk(vbr, raw_image)
    record_headers = get_record_headers(mft_chunk, vbr)
    apply_fixup(mft_chunk, record_headers, vbr)
    if is_first_record_valid(record_headers[0]):
        attribute_headers = get_attribute_headers(mft_chunk, record_headers[0])
        mft_data_attribute_header = next(get_attribute_header(attribute_headers, "DATA"))
        return get_non_resident_attribute(vbr, raw_image, mft_chunk, mft_data_attribute_header)


def get_mft_chunks(vbr, mft_data_attribute_stream):
    mft_data_attribute_stream.seek(0)
    while current_chunk := mft_data_attribute_stream.read(vbr["BytsPerMftChunk"]):
        yield current_chunk
