"""
    Provides functions for working with NTFS volumes
    Author: Harel Segev
    05/16/2020
"""
from dataruns import NonResidentStream, get_dataruns
from io import BytesIO

from construct import Struct, ConstError
from construct import Padding, Computed, Enum, Const, BytesInteger, PaddedString, If, Array
from construct import Int8ul, Int16ul, Int32ul, Int64ul
from construct import Int8sl
from construct.core import StreamError


class EmptyNonResidentAttributeError(ValueError):
    pass


BOOT_SECTOR = Struct(
    Padding(11),
    "BytsPerSec" / Int16ul,
    "SecPerClus" / Int8ul,
    Padding(34),
    "MftClusNumber" / Int64ul,
    Padding(8),
    "BytsOrClusPerRec" / Int8sl,
    "BytsPerRec" / Computed(lambda this:
                            this.BytsOrClusPerRec * this.SecPerClus * this.BytsPerSec if this.BytsOrClusPerRec > 0
                            else 2 ** abs(this.BytsOrClusPerRec)),
    Padding(3),
    "BytsOrClusPerIndx" / Int8sl,
    "BytsPerIndx" / Computed(lambda this:
                             this.BytsOrClusPerIndx * this.SecPerClus * this.BytsPerSec if this.BytsOrClusPerIndx > 0
                             else 2 ** abs(this.BytsOrClusPerIndx)),
    Padding(443)
)

END_OF_RECORD_SIGNATURE = b'\xFF\xFF\xFF\xFF'
END_OF_RECORD_SIGNATURE_LENGTH = 4

FILE_REFERENCE = Struct(
    "FileRecordNumber" / BytesInteger(6, swapped=True, signed=False),
    "SequenceNumber" / Int16ul
)

FLAGS_IN_USE = 0x01
FLAGS_DIRECTORY = 0x02


FILE_RECORD_HEADER = Struct(
    "Magic" / Const(b'FILE'),
    Padding(12),
    "SequenceNumber" / Int16ul,
    Padding(2),
    "OffsetToFirstAttribute" / Int16ul,
    "Flags" / Int16ul,
    Padding(8),
    "BaseRecordReference" / FILE_REFERENCE,
    "NextAttributeId" / Int16ul,
    "OffsetInCluster" / Computed(lambda this: this._.offset)
)

FILE_RECORD_FIXUP = Struct(
    "Magic" / Const(b'FILE'),
    "UpdateSequenceOffset" / Int16ul,
    "UpdateSequenceSize" / Int16ul,
    Padding(lambda this: this.UpdateSequenceOffset - 8),
    "UpdateSequenceNumber" / Int16ul,
    "UpdateSequenceArray" / Array(lambda this: this.UpdateSequenceSize - 1, Int16ul)
)

ATTRIBUTE_HEADER = Struct(
    "Type" / Enum(Int32ul,
                  STANDARD_INFORMATION=0x10,
                  ATTRIBUTE_LIST=0x20,
                  FILE_NAME=0x30,
                  OBJECTID=0x40,
                  SECURITY_DESCRIPTOR=0x50,
                  VOLUME_NAME=0x60,
                  VOLUME_INFORMATION=0x70,
                  DATA=0x80,
                  INDEX_ROOT=0x90,
                  INDEX_ALLOCATION=0xA0,
                  BITMAP=0xB0,
                  REPARSE_POINT=0xC0,
                  EA_INFORMATION=0xD0,
                  EA=0xE0,
                  LOGGED_UTILITY_STREAM=0x100),
    "Length" / Int32ul,
    "NonResidentFlag" / Enum(Int8ul, RESIDENT=0x00, NON_RESIDENT=0x01),
    "NameLength" / Int8ul,
    "NameOffset" / Int16ul,
    Padding(2),
    "AttributeId" / Int16ul,
    "AttributeLength" / If(lambda this: this.NonResidentFlag == "RESIDENT", Int32ul),
    "AttributeOffset" / If(lambda this: this.NonResidentFlag == "RESIDENT", Int16ul),
    Padding(16),
    "DataRunsOffset" / If(lambda this: this.NonResidentFlag == "NON_RESIDENT", Int16ul),
    Padding(6),
    "AllocatedSize" / If(lambda this: this.NonResidentFlag == "NON_RESIDENT", Int64ul),
    "RealSize" / If(lambda this: this.NonResidentFlag == "NON_RESIDENT", Int64ul),
    "OffsetInCluster" / Computed(lambda this: this._.offset)
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
    "FilenameNamespace" / Int8ul,
    "FilenameInUnicode" / PaddedString(lambda this: this.FilenameLengthInCharacters * 2, "utf16")
)


SYSTEM_PARTITION = r"\\.\C:"
ROOT_RECORD_INDEX = 5
START_OF_USER_FILES = 16
NUM_OF_FIXUP_BYTES = 2


def get_boot_sector(raw_partition):
    raw_partition.seek(0)
    return BOOT_SECTOR.parse_stream(raw_partition)


def _get_mft_offset(vbr):
    return vbr["MftClusNumber"] * vbr["SecPerClus"] * vbr["BytsPerSec"]


def _get_first_mft_cluster(vbr, raw_partition):
    record_offset = _get_mft_offset(vbr)
    raw_partition.seek(record_offset)
    return bytearray(raw_partition.read(vbr["BytsPerSec"] * vbr["SecPerClus"]))


def apply_file_record_fixup(mft_cluster, vbr):
    for record_offset in range(0, vbr["BytsPerSec"] * vbr["SecPerClus"], vbr["BytsPerRec"]):
        try:
            record_fixup = FILE_RECORD_FIXUP.parse(mft_cluster[record_offset:record_offset + vbr["BytsPerRec"]])
        except ConstError:
            continue

        for i, usn_offset_in_record in enumerate(range(vbr["BytsPerSec"] - NUM_OF_FIXUP_BYTES, vbr["BytsPerRec"], vbr["BytsPerSec"])):
            usn_offset_in_cluster = usn_offset_in_record + record_offset
            mft_cluster[usn_offset_in_cluster:usn_offset_in_cluster + NUM_OF_FIXUP_BYTES] = Int16ul.build(record_fixup["UpdateSequenceArray"][i])

    return BytesIO(mft_cluster)


def get_record_headers(mft_cluster, vbr):
    for i in range(0, vbr["BytsPerSec"] * vbr["SecPerClus"], vbr["BytsPerRec"]):
        try:
            mft_cluster.seek(i)
            yield FILE_RECORD_HEADER.parse_stream(mft_cluster, offset=i)
        except ConstError:
            yield None


def is_directory(record_header):
    return record_header["Flags"] & FLAGS_DIRECTORY == FLAGS_DIRECTORY


def get_sequence_number(record_header):
    return record_header["SequenceNumber"]


def is_base_record(record_header):
    return record_header["BaseRecordReference"]["FileRecordNumber"] == 0


def get_base_record_reference(record_header):
    base_reference = record_header["BaseRecordReference"]
    return base_reference["FileRecordNumber"], base_reference["SequenceNumber"]


def _is_end_of_record(mft_cluster, offset):
    try:
        mft_cluster.seek(offset)
        return mft_cluster.read(END_OF_RECORD_SIGNATURE_LENGTH) == END_OF_RECORD_SIGNATURE
    except StreamError:
        return True


def get_attribute_headers(mft_cluster, record_header):
    next_offset = record_header["OffsetToFirstAttribute"] + record_header["OffsetInCluster"]
    while not _is_end_of_record(mft_cluster, next_offset):
        try:
            mft_cluster.seek(next_offset)
            res = ATTRIBUTE_HEADER.parse_stream(mft_cluster, offset=next_offset)
            next_offset += res["Length"]
            yield res
        except StreamError:
            return


def get_resident_attribute(mft_cluster, attribute_header):
    mft_cluster.seek(attribute_header["OffsetInCluster"] + attribute_header["AttributeOffset"])
    return mft_cluster.read(attribute_header["AttributeLength"])


def get_attribute_type(attribute_header):
    """
    Get the type of an attribute
    :param attribute_header: the attribute header of your attribute
    :return: the attribute's type
    """
    return attribute_header["Type"]


def get_attribute_name(mft_cluster, attribute_header):
    """
    Get the name of an attribute. Doesn't work on unnamed attributes!
    :param mft_cluster: raw mft cluster
    :param attribute_header: attribute header of your attribute
    :return: the name of your attribute
    """
    mft_cluster.seek(attribute_header["OffsetInCluster"] + attribute_header["NameOffset"])
    name_length = 2 * attribute_header["NameLength"]
    return PaddedString(name_length, "utf16").parse(mft_cluster.read(name_length))


def is_resident(attribute_header):
    """
    Determine if an attribute is resident
    :param attribute_header: the attribute header of your attribute
    :return: True if resident, false otherwise
    """
    return attribute_header["NonResidentFlag"] == "RESIDENT"


def get_attribute_header(attribute_headers, attribute_type):
    """
    Get a specific attribute header from an attribute header list
    :param attribute_headers: an attribute header list
    :param attribute_type: the attribute's type
    :return: an attribute header of the type specified
    """
    for attribute_header in attribute_headers:
        if attribute_header["Type"] == attribute_type:
            return attribute_header


def parse_filename_attribute(filename_attribute):
    return FILENAME_ATTRIBUTE.parse(filename_attribute)


def get_non_resident_attribute(vbr, raw_partition, mft_cluster, attribute_header):
    """
    Get an attribute object from an attribute header, for non resident attributes only
    :param vbr: the NTFS volume's VBR object
    :param raw_partition: file object for the raw partition
    :param mft_cluster: the raw mft cluster
    :param attribute_header: the attribute header of your attribute
    :return: a stream object for your non resident attribute
    """
    if attribute_header["AllocatedSize"] == 0 or attribute_header["RealSize"] == 0:
        raise EmptyNonResidentAttributeError

    dataruns = get_dataruns(mft_cluster, attribute_header["OffsetInCluster"] + attribute_header["DataRunsOffset"])
    return NonResidentStream(vbr["BytsPerSec"] * vbr["SecPerClus"], raw_partition, dataruns)


def get_mft_data_attribute(vbr, raw_partition):
    mft_cluster = _get_first_mft_cluster(vbr, raw_partition)
    mft_cluster = apply_file_record_fixup(mft_cluster, vbr)
    record_headers = get_record_headers(mft_cluster, vbr)
    attribute_headers = get_attribute_headers(mft_cluster, next(record_headers))
    mft_data_attribute_header = get_attribute_header(attribute_headers, "DATA")
    return get_non_resident_attribute(vbr, raw_partition, mft_cluster, mft_data_attribute_header)


def get_mft_clusters(vbr, mft_data_attribute_stream):
    cluster_size = vbr["BytsPerSec"] * vbr["SecPerClus"]
    mft_data_attribute_stream.seek(0)
    while current_cluster := mft_data_attribute_stream.read(cluster_size):
        yield current_cluster
