# INDXRipper
INDXRipper is a tool for carving file metadata from NTFS $I30 indexes. It's fast, and the output is easy to integrate into a timeline!

![screenshot](https://user-images.githubusercontent.com/84273110/118458300-42e4ae00-b703-11eb-8e59-bcb9de00ca89.png)

A timeline created using mactime.pl on the combined output of INDXRipper and fls.  
See: [sleuthkit](https://github.com/sleuthkit/sleuthkit)

## Motivation

In NTFS, $INDEX_ALLOCATION attributes are used to keep track of the files in a folder. A folder's $INDEX_ALLOCATION attribute contains an entry for every file in that folder. These entries are called index entries, and they contain some of the file's metadata:
* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

$INDEX_ALLOCATION attributes often contain a significant amount of slack space, which may contain index entries of deleted files. A file's index entry may last long after the file's MFT record is lost. Finding these index entries may help you prove a file existed on a system.

## Installation

Python 3.9 or above is required.  
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct.

```bash
pip install construct==2.10.67
```
Alternatively, you can use the Windows packaged release. 

## Usage Examples

```bash
# process the partition in sector 1026048, allocated directories only
python INDXRipper.py -o 1026048 raw_disk.dd output.csv

# process a partition image, get all index entries
python INDXRipper.py --deleted-dirs ntfs.001 output.csv

# process the D: drive, --slack-only mode, bodyfile output, append "D:" to all the paths
python INDXRipper.py -m D: -w bodyfile --slack-only  \\.\D: output.bodyfile
```
### Creating a super timeline

INDXRipper is best used in combination with other tools to create a super timeline. The **--slack-only** switch should filter out data that might not be necessary in your timeline if you use fls or an MFT parser. you may also use the **--dedup** switch, and the bodyfile output option.

```bash
# fls from the sleuthkit
fls -o 128 -m C: -r image.raw > temp.bodyfile

# INDXRipper will append its output to the end of temp.bodyfile
INDXRipper -o 128 -m C: -w bodyfile --deleted-dirs --slack-only --dedup image.raw temp.bodyfile

mactime -z UTC -b temp.bodyfile > image.timeline
```

https://www.youtube.com/watch?v=0HT1uiP-BRg

#### The bodyfile output

Note that the bodyfile format is specific to the sleuthkit and is not fully documented. INDXRipper's bodyfile output is not fully compatible with it.

## Features and Details

### Basic features
* Applies fixups for index records and MFT records
* Handles $INDEX_ALLOCATION and $FILE_NAME attributes in extension records
* Full paths are reconstructed using the parent directory references from the MFT records.
* Works on live Windows NTFS drives, using device paths
* All times outputted are in UTC

### The --slack-only switch

The name of this switch is really not that great. For allocated directories, not all the entries in slack space are outputted in this mode. Moreover, if you combine it with the --deleted-dirs switch, you may find allocated entries in your output!

A lot of the entries in slack space are old entries of active files. Those old entries contain a "snapshot" of the file's metadata from an earlier point in time. Although this information may be useful in some cases, most of the time it is not necessary to answer my investigative questions.

In --slack-only mode, **some** of those entries are filtered out, to prevent information overflow in your timeline. The filtering is done as follows:

For every entry in slack space, INDXRipper scans the directory for an allocated entry with the same file name. If such entry is found, INDXRipper compares the file references in the two entries. If they match, the slack entry is not outputted. In any other case, the slack entry is outputted.

This only happens for active directories, though.  In a deleted directory, all the entries found will be outputted - including allocated ones.

### The --deleted-dirs switch

A deleted directory may have some of its clusters overwritten, either by a file - or by another directory. This means the index records found in a deleted directory may actually belong to a different directory.

INDXRipper resolves the full path for the files in each index record separately, based on the parent file reference field of the first entry in the record. This means files should always be placed in their correct paths.

#### Partial paths

Some files and folders may be listed under **/$Orphan**. This means they are deleted, their parent folder is also deleted, and their full path could not be resolved.

If a file is listed under **\<Unknown\>**, on the other hand, it doesn't mean it's deleted. The entry was found in a deleted directory, and INDXRipper could not determine its parent directory. 

## Limitations
* The tool may give false results.
* Partially overwritten entries may not be found. If they are found, though, the tool may give you false information.
* The tool currently supports NTFS version 3.1 only

### What this tool doesn't do
* This tool doesn't process $INDEX_ROOT attributes.
* This tool doesn't carve $INDEX_ALLOCATION attributes from unallocated space.


## License
[MIT](https://choosealicense.com/licenses/mit/)
