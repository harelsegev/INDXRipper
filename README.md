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

The slack space of these attributes may contain index entries of deleted files, even after thier MFT record was recycled. Finding these index entries may help you prove a file existed on a system.

## How it works
INDXRipper scans the MFT for records of directories that have an $INDEX_ALLOCATION attribute. If it finds such a record, it searches the attribute for file references to this record. Since the index entries in the attribute are of the directory's children, the $FILE_NAME attributes in them must contain this file reference.

This way, It is able to find entries other tools can't.  
Finding the full paths of directories is done using the parent directory reference in the $FILE_NAME attributes inside the MFT records.

## Features and Details
### Basic Features
* Applies fixups for index records and MFT records.
* Supports $INDEX_ALLOCATION and $FILE_NAME attributes in extension records
* Supports Unicode filenames
* Index entries from orphan directories are listed under "/$Orphan"
* Works on live Windows NTFS drives, using the "\\\\.\\\" notation
* All times outputted are UTC times

### The --bodyfile Switch
If the --bodyfile switch is given, INDXRipper will output a bodyfile for supertimeline creation.  
When creating a supertimeline, it is recommended to use the --deleted-only switch as well.

### The --deleted-only Switch
In addition to the parent file reference, index entries contain a file reference to their own file's MFT record.  
If the --deleted-only switch is given, INDXRipper follows this file reference. If it succeeds, the index entry is not outputted.  
This makes sure entries are outputted only if their file's MFT record was recycled.  
This reduces noise in case you combine the output with the output of fls or MFTECmd - there won't be duplicate lines for files that sill have their MFT record.

* Entries of deleted files that their MFT record **wasn't** recycled will **not** be outputted! These files' MFT records will be outputted by fls and MFTECmd.
* If an index entry was deleted and the file reference was overitten, the entry may be outputted despite the file having an MFT record. This is quite common actually, so you will probably still see duplicate output for many files.

## Installation 
Python 3.8 or above is required.  
Use the package manager [pip](https://pip.pypa.io/en/stable/) to install construct.
```bash
pip install construct==2.10.56
```
Alternatively, you can use the Windows standalone executable. 

## Usage
```bash
# process a dead partition image, get all index entries
python INDXRipper.py ntfs_part.001 output.csv

# process the D: drive, --deleted-only mode, bodyfile output, append "D:" to all the paths
python INDXRipper.py -m D: --deleted-only --bodyfile \\.\D: output.bodyfile
```

## Limitations
* The tool may give false results.
* Entries that are partially overitten may not be found. If they are found, though, the tool may give you false information.

### What this tool doesn't do
* This tool doesn't process $INDEX_ROOT attributes. You won't see an output for every file on the volume
* This tool doesn't carve $INDEX_ALLOCATION attributes. It won't find attributes that thier MFT entry was recycled.


## License
[MIT](https://choosealicense.com/licenses/mit/)
