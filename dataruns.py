"""
    Implements a file object for non-resident attributes
    Author: Harel Segev
    05/16/2020
"""

from construct import Struct, BitStruct, Nibble, BytesInteger, Const, Sequence, Select, RepeatUntil, Seek, this
from io import BytesIO

END_MARKER = b"\x00"

DATA_RUN = Struct(
    "Header" / BitStruct(
        "Offset" / Nibble,
        "Length" / Nibble,
    ),

    "Length" / BytesInteger(this.Header.Length, swapped=True, signed=False),
    "Offset" / BytesInteger(this.Header.Offset, swapped=True, signed=True)
).compile()

DATA_RUNS = Sequence(
    Seek(lambda this: this._.dataruns_offset),
    RepeatUntil(lambda obj, lst, ctx: obj == END_MARKER, Select(Const(END_MARKER), DATA_RUN))
)


def get_dataruns(mft_chunk, offset):
    return correct_offsets(DATA_RUNS.parse(mft_chunk, dataruns_offset=offset)[1][:-1])


def correct_offsets(dataruns):
    index = 1
    while index < len(dataruns):
        dataruns[index].Offset += dataruns[index - 1].Offset
        index += 1

    return dataruns


class NonResidentStream(BytesIO):
    def __init__(self, bytes_per_cluster, partition_offset, raw_image, dataruns, is_allocated):
        super().__init__()
        self.raw_image = raw_image
        self.bytes_per_cluster = bytes_per_cluster
        self.partition_offset = partition_offset

        self.dataruns = dataruns
        self.dataruns_index = 0

        self.physical_offset = self.current_datarun_offset()
        self.virtual_offset = 0
        self.offset_in_datarun = 0

        self.is_allocated = is_allocated

    def current_datarun(self):
        return self.dataruns[self.dataruns_index]

    def current_datarun_length(self):
        return self.current_datarun().Length * self.bytes_per_cluster

    def current_datarun_offset(self):
        return self.current_datarun().Offset * self.bytes_per_cluster

    def jump_to_next_datarun(self):
        if self.dataruns_index == len(self.dataruns) - 1:
            return False

        self.dataruns_index += 1
        self.physical_offset = self.current_datarun_offset()
        self.offset_in_datarun = 0
        return True

    def increment_offsets(self, bytes_read):
        self.physical_offset += bytes_read
        self.virtual_offset += bytes_read
        self.offset_in_datarun += bytes_read

    def bytes_to_end_of_current_datarun(self):
        return self.current_datarun_length() - self.offset_in_datarun

    def bytes_to_read(self, size):
        return min(self.bytes_to_end_of_current_datarun(), size)

    def read_bytes(self, bytes_to_read):
        self.raw_image.seek(self.physical_offset + self.partition_offset)
        return self.raw_image.read(bytes_to_read)

    def read_helper(self, size):
        res = bytearray()

        while True:
            bytes_to_read = self.bytes_to_read(size)
            res += self.read_bytes(bytes_to_read)
            self.increment_offsets(bytes_to_read)
            size -= bytes_to_read

            if size == 0 or not self.jump_to_next_datarun():
                break

        return res

    def my_size(self):
        return sum([datarun.Length for datarun in self.dataruns]) * self.bytes_per_cluster

    def read(self, size=-1):
        if size == -1:
            size = self.my_size()

        return self.read_helper(size)

    def tell(self):
        return self.virtual_offset
