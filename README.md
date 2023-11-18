# INDXRipper
INDXRipper is a tool for carving file metadata from NTFS $I30 indexes. It's (relatively) fast, and the output is easy to integrate into a timeline!

![screenshot](https://github.com/harelsegev/INDXRipper/assets/84273110/922a682c-5365-4962-98d0-c7d49521d713)

A snippet from a timeline created using mactime, INDXRipper and fls. The image used to create this timeline is included in [INDXRipper releases](https://github.com/harelsegev/INDXRipper/releases). Download it and try to [create the timeline yourself!](#using-the-sleuth-kit)

## Motivation

In NTFS, $INDEX_ALLOCATION attributes are used to keep track of which files are in which folder. A directory's $INDEX_ALLOCATION attribute contains an entry for every file in that directory. Those entries are called index entries, and they contain some of the file's metadata:
* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

$INDEX_ALLOCATION attributes often contain a significant amount of slack space, which may contain index entries of deleted files. A file's index entry may last long after its MFT record is lost. Finding index entries in slack space may help you prove a file existed on a system.

For a more detailed explanation of this artifact, watch this 13Cubed episode:  
https://www.youtube.com/watch?v=x-M-wyq3BXA

## Installation
Using the Windows [packaged releases](https://github.com/harelsegev/INDXRipper/releases) is the easiest way to get started. 


### Creating a Development Environment
Python 3.9 or above is required. INDXRipper should work with both the CPython and PyPy implementations. PyPy achieves better performance, but it doesn't allow execution against mounted NTFS volumes on Windows.

Clone the repository:
```bash
git clone https://github.com/harelsegev/INDXRipper.git
```

Create a virtualenv and use [pip](https://pip.pypa.io/en/stable/) to install construct:
```bash
cd INDXRipper
python3.9 -m pip install virtualenv

python3.9 -m virtualenv venv
source venv/bin/activate

pip install construct==2.10.69
```
Execute INDXRipper in the virtual environment:
```bash
# should print version information
venv/bin/python INDXRipper.py -V

# should also work when executed as root
sudo venv/bin/python INDXRipper.py -V
```

## Usage Examples

```bash
# process an image mounted and mapped as J: drive (Windows packaged version)
INDXRipper.exe \\.\J: outfile.csv

# process a full disk image, specifying the offset of an NTFS partition, in sectors
python INDXRipper.py -o 1026048 raw_disk.dd output.csv

# process a partition image. specifying the offset isn't required
python INDXRipper.py ntfs.001 output.csv

# process the D: drive on a live system, --no-active-files mode, bodyfile output, prepend "D:" to all the paths
python INDXRipper.py -m D: -f bodyfile --no-active-files \\.\D: output.bodyfile
```
### Creating a super timeline

INDXRipper is best used in combination with other tools to create a super timeline. The **--no-active-files** switch should filter out data that might not be necessary in your timeline if you use fls or an MFT parser. You may also use the **--dedup** switch, and the bodyfile output option.

#### Using The Sleuth Kit

```bash
# fls from the sleuth kit
fls -o 128 -m C: -r image.raw > temp.bodyfile

# INDXRipper will append its output to the end of temp.bodyfile
INDXRipper -o 128 -m C: -f bodyfile --no-active-files --dedup image.raw temp.bodyfile

mactime -z UTC -b temp.bodyfile > image.timeline
```

#### Using Plaso

```bash
# output to a new file
INDXRipper.py -o 128 -f bodyfile --no-active-files --dedup image.raw temp.bodyfile

# add the output to an existing plaso storage file
log2timeline.py --parsers mactime --storage-file storage.plaso temp.bodyfile
```

Note that the bodyfile format is specific to the sleuthkit and is not fully documented. INDXRipper's bodyfile output is not fully compatible with it.

## Features and Details

### Basic features
* Applies fixups for index records and MFT records
* Handles attributes in extension records
* Full file paths are resolved based on data from the MFT
* Works on live Windows NTFS drives, using device paths
* All the outputted timestamps are in UTC

### The --no-active-files switch

In this mode, INDXRipper will filter out both allocated and slack entries of active files.

A lot of the entries in slack space are old entries of active files. Those old entries contain a "snapshot" of the file's metadata from an earlier point in time. Although this information may be useful in some cases, most of the time it's the deleted files I'm interested in.

In --no-active-files mode, **some** of those slack entries (not all of them!) are filtered out, to prevent information overflow in your timeline. The filtering is done as follows:

For every entry in slack space, INDXRipper scans the directory for an allocated entry with the same file name. If such an entry is found, INDXRipper compares the file references in the two entries. If they match, the slack entry is not outputted.

This only happens for active directories, though. When parsing the $INDEX_ALLOCATION attribute of a deleted directory, INDXRipper will output all the entries it finds - including the ones in "allocated" space.

### The --skip-deleted-dirs switch

In this mode, INDXRipper will only parse $INDEX_ALLOCATION attributes of active directories.

A deleted directory may have some of its clusters overwritten - either by a file, or by another directory. This means the index records in a deleted directory may be partially overwritten, or may actually belong to a different directory.

When parsing the $INDEX_ALLOCATION attribute of a deleted directory, INDXRipper resolves the full path for the files in each index record separately, based on the parent file reference field of the first entry in the record. This means files should always be placed in their correct path.

#### Partial paths

Some files and folders may be listed under **/$Orphan**. This means they are deleted, their parent folder is also deleted, and their full path could not be resolved.

Files listed under **\<Unknown\>**, on the other hand - are not necessarily deleted. It means the entries were found while parsing an index record in a deleted directory, and INDXRipper could not determine the parent directory of the files.

## Limitations
* The tool may give false results. While false positives are rare, [they are possible](https://harelsegev.github.io/posts/i30-parsers-output-false-entries.-heres-why/).
* Partially overwritten entries may not be found. If they are found, though, the tool may give you false information.
* The tool supports NTFS version 3.1 only

### What this tool doesn't do
* This tool doesn't parse $INDEX_ROOT attributes.
* This tool doesn't carve INDX records from unallocated space.


## License
[MIT](https://choosealicense.com/licenses/mit/)
