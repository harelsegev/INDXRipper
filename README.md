# INDXRipper
Find index entries in $INDEX_ALLOCATION attributes

![screenshot](https://user-images.githubusercontent.com/84273110/118458300-42e4ae00-b703-11eb-8e59-bcb9de00ca89.png)

Timeline created using mactime.pl on the combined output of INDXRipper and fls.  
See: [sleuthkit](https://github.com/sleuthkit/sleuthkit)
## Motivation
$INDEX_ALLOCATION attributes are used by NTFS directories to store index entries for files in the directory.

A file's index entry contains the following information:

* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

The slack space of these attributes may contain index entries of deleted files, even after their MFT record was recycled. Finding these index entries may help you prove a file existed on a system.
## Why another tool?
The data in the slack space of $INDEX_ALLOCATION attributes is valuable, yet it is not always viable to collect and parse it. While the $MFT file can be quickly collected and parsed - using many different tools, existing tools for carving index entries from $INDEX_ALLOCATION slack space are time intensive, and there aren't as many of them.
## How does it work?
INDXRipper scans the MFT for records of directories that have an $INDEX_ALLOCATION attribute. If it finds such a record, it searches the attribute for file references to that record. The index entries in the attribute are of the directory's children, so the $FILE_NAME attributes in them should contain this file reference.

## Features and Details
### Basic Features
* Applies fixups for index records and MFT records.
* Supports $INDEX_ALLOCATION and $FILE_NAME attributes in extension records
* Supports Unicode filenames
* The full paths of directories are determined using the parent directory references from the MFT records.
* Orphan directories are listed under "/$Orphan"
* Works on live Windows NTFS drives, using "\\\\.\\" paths (device paths)
* All times outputted are UTC times

### Super timeline creation
INDXRipper is best used in combination with other tools to create a super timeline.  
For this purpose, the **--invalid-only**, **--bodyfile**, and the **--dedup** switches may be useful.

#### The --invalid-only switch
If the --invalid-only switch is given, INDXRipper will only output index entries with an invalid file reference - omitting many of the files you'll already have output for in an MFT timeline. Use this switch for integration with fls or MFTECmd.
* Not all deleted files will be outputted, only ones that lost their MFT record.
* You **will** probably see output for files that still have their MFT record, and are (sometimes) not even deleted! This happens because NTFS moves the index entries around to keep them sorted, so there are unallocated entries for active files. The file reference in those entries may be overwritten and become invalid, causing the entry to be outputted, despite the file being active.

#### The --bodyfile and --dedup switches
* The --bodyfile switch will output a bodyfile, for integration with other tools that produce a bodyfile.
* The --dedup switch will deduplicate output lines. This is useful because INDXRipper may find multiple identical entries, due to index entry reallocation.

## Installation 
Python 3.8 or above is required.  
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct.
```bash
pip install construct==2.10.67
```
Alternatively, you can use the Windows standalone executable. 

## Usage
```bash
# process the partition in sector 1026048, get all index entries
python INDXRipper.py -o 1026048 raw_disk.dd output.csv

# process a partition image, get all index entries
python INDXRipper.py ntfs.part.001 output.csv

# process the D: drive, --invalid-only mode, bodyfile output, append "D:" to all the paths
python INDXRipper.py -m D: --invalid-only --bodyfile \\.\D: output.bodyfile
```
https://www.youtube.com/watch?v=0HT1uiP-BRg

## Limitations
* The tool may give false results.
* Entries that are partially overwritten may not be found. If they are found, though, the tool may give you false information.

### What this tool doesn't do
* This tool doesn't process $INDEX_ROOT attributes. You won't see an output for every file on the volume
* This tool doesn't carve $INDEX_ALLOCATION attributes. It won't find attributes that their MFT entry was recycled.


## License
[MIT](https://choosealicense.com/licenses/mit/)
