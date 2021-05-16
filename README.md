# INDXRipper
Find index entries in $INDEX_ALLOCATION attributes

## Motivation
$INDEX_ALLOCATION attributes are used by NTFS to store index entries for files in a directory.

A file's index entry contains the following information:

* File name
* File size
* Allocated size of file (size on disk)
* A set of MACB timestamps

The slack space of these attributes may contain index entries of deleted files, even after thier MFT record was recycled. Finding these index entries may help you prove a file existed on a system, and get some of it's metadata

## How it works
INDXRipper scans the MFT for records of directories that have an $INDEX_ALLOCATION attribute. If it finds such record, it searches the attribute for file references to this record. Since the index entries in the attribute represent children of the directory, the $FILE_NAME attributes in them must contain this file reference.

This way, It is able to find entries most other tools aren't.  
Finding the full paths of directories is done by using the parent directory reference in $FILE_NAME attributes of the MFT file records.

## Features and Details
These are pretty standard but here's a list anyways
* Applies fixups for index records and mft records.
* Supports $INDEX_ALLOCATION and $FILE_NAME attributes in extension records
* Supports unicode filenames
* Index entries from orphan directories are listed under "/$Orphan"
* Provides bodyfile output for supertimeline creation
* works on live windows systems using the "\\\\.\\\" notation
* all times outputted are UTC times

## Installation 
Python 3.8 or above is required.  
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct.
```bash
pip install construct==2.10.56
```
Alternatively, you can use the Windows standalone executable. 

## Usage
```bash
# process dead disk image, get all index entries
python INDXRipper.py ntfs.part.001 output.csv

# process live system, slack space only, bodyfile output, append "C:" to all the paths
python INDXRipper.py -m C: --slack-only --bodyfile \\.\C: output.bodyfile
```

## Limitations
* This tool may give false results
* This tool may not find entries that are partially overwritten
* This tool doesn't process $INDEX_ROOT attributes. You won't see an output for every file on the volume
* This tool doesn't carve $INDEX_ALLOCATION attributes. It won't find attributes that thier MFT entry was recycled.


## License
[MIT](https://choosealicense.com/licenses/mit/)
