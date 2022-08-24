# INDXRipper
INDXRipper is a tool for carving file metadata from NTFS $I30 indexes. It's fast, and the output is easy to integrate into a timeline!

![screenshot](https://user-images.githubusercontent.com/84273110/118458300-42e4ae00-b703-11eb-8e59-bcb9de00ca89.png)

A timeline created using mactime.pl on the combined output of INDXRipper and fls.  
See: [sleuthkit](https://github.com/sleuthkit/sleuthkit)

## Motivation

In NTFS, $INDEX_ALLOCATION attributes are used to keep track of which files are in which folder. A folder's $INDEX_ALLOCATION attribute contains an entry for every file in that folder. Those entries are called index entries, and they contain some of the file's metadata:
* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

$INDEX_ALLOCATION attributes often contain a significant amount of slack space, which may contain index entries of deleted files. A file's index entry may last long after its MFT record is lost. Finding these index entries may help you prove a file existed on a system.

For a more detailed explanation of this artifact, watch this 13Cubed episode:  
https://www.youtube.com/watch?v=x-M-wyq3BXA

## Installation
Using the Windows [packaged releases](https://github.com/harelsegev/INDXRipper/releases) is the easiest way to get started.

### I use Linux
Clone the repository:
```bash
git clone https://github.com/harelsegev/INDXRipper.git
```

Python 3.9 or above is required. Create a virtualenv and use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct:
```bash
cd INDXRipper
python3.9 -m pip install virtualenv

python3.9 -m virtualenv venv
source venv/bin/activate

pip install construct==2.10.68
```
Now, you can execute INDXRipper:
```bash
# should print version information
venv/bin/python INDXRipper.py -V

# should also work when executed as root
sudo venv/bin/python INDXRipper.py -V
```

## Usage Examples

```bash
# process an image mounted and mapped as J: drive (Windows version)
INDXRipper.exe \\.\J: outfile.csv

# process the partition in sector 1026048, allocated directories only
python INDXRipper.py -o 1026048 raw_disk.dd output.csv

# process a partition image, get all index entries
python INDXRipper.py --deleted-dirs ntfs.001 output.csv

# process the D: drive, --slack-only mode, bodyfile output, append "D:" to all the paths
python INDXRipper.py -m D: -w bodyfile --slack-only  \\.\D: output.bodyfile
```
### Creating a super timeline

INDXRipper is best used in combination with other tools to create a super timeline. The **--slack-only** switch should filter out data that might not be necessary in your timeline if you use fls or an MFT parser. you may also use the **--dedup** switch, and the bodyfile output option.

#### Using the sleuthkit

```bash
# fls from the sleuthkit
fls -o 128 -m C: -r image.raw > temp.bodyfile

# INDXRipper will append its output to the end of temp.bodyfile
INDXRipper -o 128 -m C: -w bodyfile --deleted-dirs --slack-only --dedup image.raw temp.bodyfile

mactime -z UTC -b temp.bodyfile > image.timeline
```

#### Using Plaso

```bash
# output to a new file
INDXRipper.py -o 128 -w bodyfile --deleted-dirs --slack-only --dedup image.raw temp.bodyfile

# add the output to an existing plaso storage file
log2timeline.py --parsers mactime --storage-file storage.plaso temp.bodyfile
```

Note that the bodyfile format is specific to the sleuthkit and is not fully documented. INDXRipper's bodyfile output is not fully compatible with it.

## Features and Details

### Basic features
* Applies fixups for index records and MFT records
* Handles $INDEX_ALLOCATION and $FILE_NAME attributes in extension records
* Full paths are resolved using the parent directory references from the MFT records.
* Works on live Windows NTFS drives, using device paths
* All times outputted are in UTC

### The --slack-only switch

Not all the entries in slack space are outputted in this mode.

A lot of the entries in slack space are old entries of active files. Those old entries contain a "snapshot" of the file's metadata from an earlier point in time. Although this information may be useful in some cases, most of the time it's the deleted files I'm interested in.

In --slack-only mode, **some** of those entries are filtered out, to prevent information overflow in your timeline. The filtering is done as follows:

For every entry in slack space, INDXRipper scans the directory for an allocated entry with the same file name. If such entry is found, INDXRipper compares the file references in the two entries. If they match, the slack entry is not outputted.

This only happens for active directories, though.  In a deleted directory, all the entries found will be outputted - including allocated ones.

### The --deleted-dirs switch

A deleted directory may have some of its clusters overwritten - either by a file, or by another directory. This means the index records found in a deleted directory may be partially overwritten, or may actually belong to a different directory.

In a deleted directory, INDXRipper resolves the full path for the files in each index record separately, based on the parent file reference field of the first entry in the record. This means files should be placed in their correct path.

#### Partial paths

Some files and folders may be listed under **/$Orphan**. This means they are deleted, their parent folder is also deleted, and their full path could not be resolved.

Files listed under **\<Unknown\>**, on the other hand - are not necessarily deleted. Those entries were found in a deleted directory, and INDXRipper could not determine their parent directory. 

## Limitations
* The tool may give false results. While false positives are rare, they are possible.
* Partially overwritten entries may not be found. If they are found, though, the tool may give you false information.
* The tool supports NTFS version 3.1 only

### What this tool doesn't do
* This tool doesn't process $INDEX_ROOT attributes.
* This tool doesn't carve $INDEX_ALLOCATION attributes from unallocated space.


## License
[MIT](https://choosealicense.com/licenses/mit/)
