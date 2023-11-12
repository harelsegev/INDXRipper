"""
    Provides functions for working with NTFS volumes
    Author: Harel Segev
    05/16/2020
"""

from construct import Struct, Padding, Computed, IfThenElse, BytesInteger, Const, Switch, Tell, FlagsEnum
from construct import Pointer, Seek, RepeatUntil, Adapter, Bytes, Select, Sequence, Enum, Array
from construct import Int8ul, Int16ul, Int32ul, Int64ul, Int8sl, this, ConstError

from dataruns import get_dataruns, NonResidentStream
from sys import exit as sys_exit
from contextlib import suppress
from io import BytesIO


class EmptyNonResidentAttributeError(ValueError):
    pass


class WideCharacterStringAdapter(Adapter):
    def _decode(self, obj, context, path):
        return obj.decode("UTF-16LE", errors="replace")


BOOT_SECTOR = Struct(
    "OffsetInImage" / Tell,
    Padding(3),
    "Magic" / Const(b"NTFS"),
    Padding(4),
    "BytsPerSec" / Int16ul,

    "SecPerClusRaw" / Int8ul,
    "SecPerClus" / IfThenElse(
        lambda this: 244 <= this.SecPerClusRaw <= 255,
        Computed(lambda this: 2 ** (256 - this.SecPerClusRaw)),
        Computed(lambda this: this.SecPerClusRaw)
    ),

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
).compile()

FILE_RECORD_HEADER = Struct(
    "OffsetInChunk" / Tell,
    "Magic" / Const(b"FILE"),

    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,

    Padding(8),
    "SequenceNumber" / Int16ul,
    Padding(2),
    "FirstAttributeOffset" / Int16ul,
    "Flags" / FlagsEnum(Int16ul, IN_USE=1, DIRECTORY=2),
    Padding(8),
    "BaseRecordReference" / FILE_REFERENCE,
    Padding(4),
    "ThisRecordIndex" / Int32ul,

    Seek(this.UpdateSequenceOffset + this.OffsetInChunk),
    "UpdateSequenceNumber" / Int16ul,
    "UpdateSequenceArray" / Array(this.UpdateSequenceSize - 1, Int16ul)
).compile()

ATTRIBUTE_HEADER = Struct(
    "OffsetInChunk" / Tell,

    "Type" / Enum(Int32ul, FILE_NAME=0x30, INDEX_ALLOCATION=0xA0, DATA=0x80),
    "Length" / Int32ul,
    "Residence" / Enum(Int8ul, RESIDENT=0x00, NON_RESIDENT=0x01),
    "NameLength" / Int8ul,
    "NameOffset" / Int16ul,
    "AttributeName" / Pointer(this.NameOffset + this.OffsetInChunk,
                              WideCharacterStringAdapter(Bytes(2 * this.NameLength))),
    Padding(4),
    "Metadata" / Switch(
        this.Residence,
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

    Seek(this.Length + this.OffsetInChunk)
).compile()

END_OF_RECORD_SIGNATURE = b"\xFF\xFF\xFF\xFF"

ATTRIBUTE_HEADERS = Sequence(
    Seek(this._.offset),
    RepeatUntil(
        lambda obj, lst, ctx: obj == END_OF_RECORD_SIGNATURE,
        Select(Const(END_OF_RECORD_SIGNATURE), ATTRIBUTE_HEADER)
    )
)


FILENAME_ATTRIBUTE = Struct(
    "ParentDirectoryReference" / FILE_REFERENCE,
    Padding(56),
    "FilenameLengthInCharacters" / Int8ul,
    "FilenameNamespace" / Enum(Int8ul, POSIX=0, WIN32=1, DOS=2, WIN32_DOS=3),
    "FilenameInUnicode" / WideCharacterStringAdapter(Bytes(this.FilenameLengthInCharacters * 2))
).compile()


def get_boot_sector(raw_image, partition_offset):
    raw_image.seek(partition_offset)

    try:
        return BOOT_SECTOR.parse_stream(raw_image)

    except ConstError:
        sys_exit("INDXRipper: error: invalid volume boot record")


def get_mft_offset(vbr):
    return vbr["MftClusNumber"] * vbr["BytsPerClus"] + vbr["OffsetInImage"]


def get_first_mft_chunk(vbr, raw_image):
    raw_image.seek(get_mft_offset(vbr))
    return bytearray(raw_image.read(vbr["BytsPerMftChunk"]))


def get_record_headers(mft_chunk, vbr):
    mft_chunk_stream = BytesIO(mft_chunk)
    for record_offset in range(0, vbr["BytsPerMftChunk"], vbr["BytsPerRec"]):
        mft_chunk_stream.seek(record_offset)

        with suppress(ConstError):
            yield FILE_RECORD_HEADER.parse_stream(mft_chunk_stream)


FIXUP_INTERVAL = 512


def apply_record_fixup(mft_chunk, record_header, vbr):
    usn = record_header["UpdateSequenceNumber"]
    first_fixup_offset = record_header["OffsetInChunk"] + FIXUP_INTERVAL - 2
    end_of_record_offset = record_header["OffsetInChunk"] + vbr["BytsPerRec"]

    for i, usn_offset in enumerate(range(first_fixup_offset, end_of_record_offset, FIXUP_INTERVAL)):
        if Int16ul.parse(mft_chunk[usn_offset:usn_offset + 2]) != usn:
            return False

        mft_chunk[usn_offset:usn_offset + 2] = Int16ul.build(record_header["UpdateSequenceArray"][i])

    return True


def is_used(record_header):
    return record_header["Flags"]["IN_USE"]


def is_directory(record_header):
    return record_header["Flags"]["DIRECTORY"]


def get_sequence_number(record_header):
    if is_used(record_header):
        return record_header["SequenceNumber"]
    else:
        return record_header["SequenceNumber"] - 1


def get_mft_index(record_header):
    return record_header["ThisRecordIndex"]


def is_base_record(record_header):
    return record_header["BaseRecordReference"]["FileRecordNumber"] == 0


def get_base_record_reference(record_header):
    base_reference = record_header["BaseRecordReference"]
    return base_reference["FileRecordNumber"], base_reference["SequenceNumber"]


def get_attribute_headers(mft_chunk, record_header):
    first_attribute_offset = record_header["FirstAttributeOffset"] + record_header["OffsetInChunk"]
    return ATTRIBUTE_HEADERS.parse(mft_chunk, offset=first_attribute_offset)[1][:-1]


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


def get_non_resident_attribute(vbr, raw_image, mft_chunk, attribute_header, is_allocated):
    dataruns_offset_in_chunk = attribute_header["OffsetInChunk"] + attribute_header["Metadata"]["DataRunsOffset"]
    dataruns = get_dataruns(mft_chunk, dataruns_offset_in_chunk)

    if not dataruns:
        raise EmptyNonResidentAttributeError

    return NonResidentStream(vbr["BytsPerClus"], vbr["OffsetInImage"], raw_image, dataruns, is_allocated)


def get_first_record_header(vbr, raw_image):
    mft_chunk = get_first_mft_chunk(vbr, raw_image)
    first_record_header = next(get_record_headers(mft_chunk, vbr))

    if not first_record_header:
        sys_exit(f"INDXRipper: error: first file record is invalid")

    if not apply_record_fixup(mft_chunk, first_record_header, vbr):
        sys_exit(f"INDXRipper: error: fixup validation failed for first file record")

    return mft_chunk, first_record_header


def get_first_mft_data_attribute(vbr, raw_image):
    mft_chunk, first_record_header = get_first_record_header(vbr, raw_image)
    attribute_headers = get_attribute_headers(mft_chunk, first_record_header)
    mft_data_attribute_header = next(get_attribute_header(attribute_headers, "DATA"))
    return get_non_resident_attribute(vbr, raw_image, mft_chunk, mft_data_attribute_header, True)


def get_mft_chunks(vbr, mft_data_attribute_stream):
    while current_chunk := mft_data_attribute_stream.read(vbr["BytsPerMftChunk"]):
        yield current_chunk
