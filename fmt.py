"""
    Output formatting
    Author: Harel Segev
    11/06/2021
"""

from datetime import timezone, datetime


def to_epoch(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).timestamp()


def to_iso(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).isoformat()


COMMON_FIELDS = {
    "index": lambda index_entry: index_entry["FILE_REFERENCE"]["FileRecordNumber"],
    "sequence": lambda index_entry: index_entry["FILE_REFERENCE"]["SequenceNumber"],

    "size": lambda index_entry: index_entry["RealSize"],
    "alloc_size": lambda index_entry: index_entry["AllocatedSize"],

    "cr_time": lambda index_entry: index_entry["CreationTime"],
    "m_time": lambda index_entry: index_entry["LastModificationTime"],
    "a_time": lambda index_entry: index_entry["LastAccessTime"],
    "c_time": lambda index_entry: index_entry["LastMftChangeTime"],
}


OUTPUT_FORMATS = {
    "csv":
    {
        "fmt": "\"{full_path}\",{flags},{comment},{index},"
               "{sequence},{size},{alloc_size},{cr_time},{m_time},{a_time},{c_time}\n",

        "header": "Path,Flags,Comment,FileNumber,SequenceNumber,Size,"
                  "AllocatedSize,CreationTime,ModificationTime,AccessTime,ChangeTime\n",

        "comment_fmt": lambda comment: comment,

        "fields":
        {
            "flags": lambda index_entry: "|".join
            (
                [flag for flag in index_entry["Flags"] if index_entry["Flags"][flag] and flag != "_flagsenum"]
            )

        } | COMMON_FIELDS,

        "adapted_fields": {"cr_time": to_iso, "m_time": to_iso, "a_time": to_iso, "c_time": to_iso}
    },

    "bodyfile":
    {
        "fmt": "0|{full_path} ($I30){comment}|{index}|"
               "{mode_prt1}{mode_prt2}|0|0|{size}|{a_time}|{m_time}|{c_time}|{cr_time}\n",

        "header": "",
        "comment_fmt": lambda comment: f" ({comment})" if comment else "",

        "fields":
        {
            "mode_prt1": lambda index_entry: "d/-" if index_entry["Flags"]["DIRECTORY"] else "r/-",
            "mode_prt2": lambda index_entry: 3 * "{}{}{}".format
            (
              "r" if not index_entry["Flags"]["READ_ONLY"] else "-",
              "w" if not index_entry["Flags"]["HIDDEN"] else "-",
              "x"
            )

        } | COMMON_FIELDS,

        "adapted_fields": {"cr_time": to_epoch, "m_time": to_epoch, "a_time": to_epoch, "c_time": to_epoch}
    }
}


def populate_fmt_dict(fmt_dict, index_entry, output_format):
    output_fields = OUTPUT_FORMATS[output_format]["fields"]
    adapted_fields = OUTPUT_FORMATS[output_format]["adapted_fields"]

    for field in output_fields:
        fmt_dict[field] = output_fields[field](index_entry)

        if field in adapted_fields:
            fmt_dict[field] = adapted_fields[field](fmt_dict[field])


def get_entry_output(index_entry, parent_path, output_format, comment):
    fmt_dict = {
        "full_path": parent_path + "/" + index_entry["FilenameInUnicode"],
        "comment": OUTPUT_FORMATS[output_format]["comment_fmt"](comment)
    }

    populate_fmt_dict(fmt_dict, index_entry, output_format)
    return OUTPUT_FORMATS[output_format]["fmt"].format(**fmt_dict)


def get_format_header(output_format):
    return OUTPUT_FORMATS[output_format]["header"]
