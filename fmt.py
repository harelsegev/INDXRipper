"""
    Output formatting
    Author: Harel Segev
    31/12/2021
"""

import os
import tempfile
from sys import stderr
from datetime import timezone, datetime


def to_epoch(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).timestamp()


def to_iso(timestamp: datetime):
    return timestamp.replace(tzinfo=timezone.utc).isoformat()


COMMON_FIELDS = {
    "parent_path": lambda index_entry: index_entry["ParentPath"],
    "filename": lambda index_entry: index_entry["FilenameInUnicode"],

    "parent_index": lambda index_entry: index_entry["ParentDirectoryReference"]["FileRecordNumber"],
    "parent_sequence": lambda index_entry: index_entry["ParentDirectoryReference"]["SequenceNumber"],

    "index": lambda index_entry: index_entry["FileReference"]["FileRecordNumber"],
    "sequence": lambda index_entry: index_entry["FileReference"]["SequenceNumber"],

    "size": lambda index_entry: index_entry["RealSize"],
    "alloc_size": lambda index_entry: index_entry["AllocatedSize"],

    "cr_time": lambda index_entry: index_entry["CreationTime"],
    "m_time": lambda index_entry: index_entry["LastModificationTime"],
    "a_time": lambda index_entry: index_entry["LastAccessTime"],
    "c_time": lambda index_entry: index_entry["LastMftChangeTime"],

    "flags": lambda index_entry: "|".join
    (
        [flag for flag in index_entry["Flags"] if index_entry["Flags"][flag] and flag != "_flagsenum"]
    ),

    "source": lambda index_entry: "Index Slack" if index_entry["IsSlack"] else "Index Record"
}

OUTPUT_FORMATS = {
    "csv":
    {
        "fmt": "{source},\"{parent_path}\",{parent_index},{parent_sequence},\"{filename}\",{flags},{index},{sequence},"
               "{size},{alloc_size},{cr_time},{m_time},{a_time},{c_time}\n",

        "header": "Source,ParentPath,ParentFileNumber,ParentSequenceNumber,Filename,Flags,FileNumber,"
                  "SequenceNumber,Size,AllocatedSize,CreationTime,ModificationTime,AccessTime,ChangedTime\n",

        "fields": {} | COMMON_FIELDS,

        "adapted_fields":
        {
            "cr_time": to_iso,
            "m_time": to_iso,
            "a_time": to_iso,
            "c_time": to_iso,

            "parent_path": lambda parent_path: parent_path.replace("\"", "\"\""),
            "filename": lambda filename: filename.replace("\"", "\"\"")
        }
    },

    "jsonl":
    {
        "fmt": "{{\"source\": \"{source}\", \"parent_path\": \"{parent_path}\", "
               "\"parent_file_number\": \"{parent_index}\", \"parent_sequence_number\": \"{parent_sequence}\", "
               "\"filename\": \"{filename}\", \"flags\": \"{flags}\", \"file_number\": \"{index}\", "
               "\"sequence_number\": \"{sequence}\", \"size\": \"{size}\", \"allocated_size\": \"{alloc_size}\", "
               "\"creation_time\": \"{cr_time}\", \"modification_time\": \"{m_time}\", \"access_time\": \"{a_time}\", "
               "\"changed_time\": \"{c_time}\"}}\n",

        "header": "",

        "fields": {} | COMMON_FIELDS,

        "adapted_fields":
        {
            "cr_time": to_iso,
            "m_time": to_iso,
            "a_time": to_iso,
            "c_time": to_iso,

            "parent_path": lambda parent_path: parent_path.replace("\"", "\\\""),
            "filename": lambda filename: filename.replace("\"", "\\\"")
        }
    },

    "bodyfile":
    {
        "fmt": "0|{full_path} ($I30){slack}|{index}|{mode_part1}"
               "{mode_part2}|0|0|{size}|{a_time}|{m_time}|{c_time}|{cr_time}\n",

        "header": "",

        "fields":
        {
            "full_path": lambda index_entry: index_entry["ParentPath"] + "/" + index_entry["FilenameInUnicode"],

            "mode_part1": lambda index_entry: "d/-" if index_entry["Flags"]["DIRECTORY"] else "r/-",
            "mode_part2": lambda index_entry: 3 * "{}{}{}".format
            (
              "r" if not index_entry["Flags"]["READ_ONLY"] else "-",
              "w" if not index_entry["Flags"]["HIDDEN"] else "-",
              "x"
            ),

            "slack": lambda index_entry: " (slack)" if index_entry["IsSlack"] else ""

        } | COMMON_FIELDS,

        "adapted_fields": {"cr_time": to_epoch, "m_time": to_epoch, "a_time": to_epoch, "c_time": to_epoch},
    }
}


def populate_fmt_dict(fmt_dict, index_entry, output_format):
    output_fields = OUTPUT_FORMATS[output_format]["fields"]
    adapted_fields = OUTPUT_FORMATS[output_format]["adapted_fields"]

    for field in output_fields:
        fmt_dict[field] = output_fields[field](index_entry)

        if field in adapted_fields:
            fmt_dict[field] = adapted_fields[field](fmt_dict[field])


def get_entry_output(index_entry, output_format):
    fmt_dict = {}
    populate_fmt_dict(fmt_dict, index_entry, output_format)
    return OUTPUT_FORMATS[output_format]["fmt"].format(**fmt_dict)


def get_format_header(output_format):
    return OUTPUT_FORMATS[output_format]["header"]


def eprint(*args, **kwargs):
    print(*args, file=stderr, **kwargs)


def warning(message):
    eprint(f"INDXRipper: warning: {message}")


def write_dedup_output_lines(output_lines, outfile, output_format):
    tempdir = os.path.dirname(outfile)

    with tempfile.TemporaryFile(mode="rt+", dir=tempdir, encoding="utf-8") as tmp:
        tmp.writelines(output_lines)

        tmp.seek(0)
        with open(outfile, "at+", encoding="utf-8") as out:
            tmp_iter = iter(tmp)

            if get_format_header(output_format):
                out.writelines(next(tmp_iter))

            out.writelines(set(tmp_iter))


def write_all_output_lines(output_lines, outfile):
    with open(outfile, "at+", encoding="utf-8") as out:
        out.writelines(output_lines)


def write_output_lines(output_lines, outfile, dedup, output_format):
    if dedup:
        write_dedup_output_lines(output_lines, outfile, output_format)
    else:
        write_all_output_lines(output_lines, outfile)
