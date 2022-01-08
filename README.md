# INDXRipper
INDXRipper is a tool for carving file metadata from NTFS $I30 indexes. It's fast, and the output is easy to integrate into a timeline!

![screenshot](https://user-images.githubusercontent.com/84273110/118458300-42e4ae00-b703-11eb-8e59-bcb9de00ca89.png)

A timeline created using mactime.pl on the combined output of INDXRipper and fls.  
See: [sleuthkit](https://github.com/sleuthkit/sleuthkit)

## Motivation

In NTFS, folders store entries for every file they contain in a special attribute, called $INDEX_ALLOCATION. These entries are called index entries, and they contain some of the file's metadata:

* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

The slack space in the $INDEX_ALLOCATION attributes may contain index entries of deleted files. Such entries may last long after the file's MFT record is lost. Finding these index entries may help you prove a file existed on a system.

## Installation 
Python 3.9 or above is required.  
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct.

```bash
pip install construct==2.10.67
```
Alternatively, you can use the Windows packaged release. 

## Usage Examples

```bash
# process the partition in sector 1026048, get all index entries
python INDXRipper.py -o 1026048 raw_disk.dd output.csv

# process a partition image, get all index entries
python INDXRipper.py ntfs.001 output.csv

# process the D: drive, --slack-only mode, bodyfile output, append "D:" to all the paths
python INDXRipper.py -m D: -w bodyfile --slack-only  \\.\D: output.bodyfile
```
### Creating a super timeline

INDXRipper is best used in combination with other tools to create a super timeline. The **--slack-only** switch should filter out data that might not be necessary in your timeline if you use fls or an MFT parser. you may also use the **--dedup** switch, and the bodyfile output option.

```bash
# fls from the sleuthkit
fls -o 128 -m C: -r image.raw > temp.bodyfile

# INDXRipper will append its output to the end of temp.bodyfile
python INDXRipper.py -o 128 -m C: -w bodyfile --slack-only --dedup image.raw temp.bodyfile

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
* Orphan directories are listed under "/$Orphan"
* Directories with no $FILE_NAME attributes in their MFT records are listed under "/$NoName"
* Works on live Windows NTFS drives, using device paths
* All times outputted are in UTC

### Carving method

INDXRipper scans the MFT for records of directories that have an $INDEX_ALLOCATION attribute. If it finds such a record, it searches the attribute for the directory's file reference. The index entries in the attribute should contain this file reference.

### The --slack-only switch

For every entry in slack space, INDXRipper scans the $INDEX_ALLOCATION attribute for an allocated entry with the same file name. If such entry is found, INDXRipper compares the file references in the two entries. If they match, the slack entry is not outputted.

In any other case, the slack entry is outputted.

* The **--slack-only** switch filters out metadata of active files. Old metadata of active files can sometimes be of interest, for example - a previous last modification time of an LNK file. Keep this in mind.

## Limitations
* The tool may give false results.
* Entries that are partially overwritten may not be found. If they are found, though, the tool may give you false information.
* The tool currently supports NTFS version 3.1 only

### What this tool doesn't do
* This tool doesn't process $INDEX_ROOT attributes.
* This tool doesn't carve $INDEX_ALLOCATION attributes from unallocated space.


## License
[MIT](https://choosealicense.com/licenses/mit/)
