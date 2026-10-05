# Table of Contents
- [Table of Contents](#table-of-contents)
- [GitHub overview](#github-overview)
- [Wear Aware](#wear-aware)
  - [Overview](#overview)
  - [Nonwear algorithm options](#nonwear-algorithm-options)
  - [Input data](#input-data)
  - [Output](#output)
  - [Method validation](#method-validation)
  - [How do I download it?](#how-do-i-download-it)
  - [How do I use it?](#how-do-i-use-it)
  - [Can anyone use Wear Aware?](#can-anyone-use-wear-aware)
  - [How do I cite using it?](#how-do-i-cite-using-it)
- [How to run Wear Aware from the code](#how-to-run-wear-aware-from-the-code)




# GitHub overview
This GitHub repository contains the code and graphical user interface for a tool called Wear Aware, used to identify wear and nonwear times from accelerometer data.

Specifically, we provide the open source code for the Wear Aware tool, a graphical user interface to run many raw accelerometer files through the selected nonwear algorithm. The directly downloadable compiled versions of Wear Aware are available under "Releases". The copyright statement and terms of use apply to all code, files, and materials provided in this repository (See [LICENSE](https://github.com/LettsE/WearAware/blob/main/LICENSE)).


# Wear Aware
## Overview
Wear Aware is a standalone tool (PyQt5 GUI) that detects accelerometer nonwear time from raw
`.gt3x` or `.csv` accelerometer files and exports wear time bouts as a csv. The output uses
the same `studyid,WearTimeStart,WearTimeEnd` format as the
[Little Movers Activity Analysis](https://github.com/LettsE/ToddlerMachineLearning) tool, so it can be used as a logbook file for Little Movers Activity Analysis.

## Nonwear algorithm options

Wear Aware provides code for seven nonwear methods. Information about which one to select for toddlers can be found in: Letts et al. 2025, *Beyond the (Log)book:
Comparing Accelerometer Nonwear Detection Techniques in Toddlers*
([doi.org/10.1111/cch.70133](https://doi.org/10.1111/cch.70133)).



| Method | Input | Epoch | Rule |
| --- | --- | --- | --- |
| `5min_0count` | counts | 1 s | ≥5 consecutive minutes of 0 ActiGraph counts |
| `10min_0count` | counts | 1 s | ≥10 consecutive minutes of 0 ActiGraph counts |
| `20min_0count` | counts | 1 s | ≥20 consecutive minutes of 0 ActiGraph counts |
| `30min_0count` | counts | 1 s | ≥30 consecutive minutes of 0 ActiGraph counts |
| `60min_0count` | counts | 1 s | ≥60 consecutive minutes of 0 ActiGraph counts |
| `Troiano60s` | counts | 60 s | ≥60 consecutive minutes of 0 ActiGraph counts, allowing up to 2 consecutive non-zero minutes ≤100 counts/min; any minute >100 counts/min is wear |
| `Ahmadi` | raw *g* | 1 s | 30-min sliding interval (1 s sliding window); considered nonwear when standard deviation of vector magnitude (*g*) per second is < 0.013 *g*; if wear period is < 30 min and < 30% of combined bordering nonwear periods, marked nonwear |

Reference for Troiano method: Troiano, R. P., D. Berrigan, K. W. Dodd, L. C. Mâsse, T. Tilert, and M. Mcdowell. 2008. “Physical Activity in the United States Measured by Accelerometer.” Medicine and Science in Sports and Exercise 40, no. 1: 181–188. https://doi.org/10.1249/mss.0b013e31815a51b3.

Reference for Ahmadi method: Ahmadi, M. N., N. Nathan, R. Sutherland, L. Wolfenden, and S. G. Trost. 2020. “Non-Wear or Sleep? Evaluation of Five Non-Wear Detection Algorithms for Raw Accelerometer Data.” Journal of Sports Sciences 38, no. 4: 399–404. https://doi.org/10.1080/02640414.2019.1703301.

If you are looking to use nonwear methods not already included in Wear Aware, please contact [Elyse Letts](https://www.elyseletts.com/) for a potential collaboration.

## Input data

Wear Aware accepts raw `.gt3x` files or `.csv`
files with one row per sample and `Datetime`/`X`/`Y`/`Z` columns (flexible
header aliases; raw acceleration in **g**, gravity present). Name each file
with the study/participant id, e.g. `participant001.csv`; the name without
its extension becomes the `studyid` in the output. 

Other notes for input csvs:
* **Column names are flexible.** Matching ignores case, spaces, underscores and bracketed units, so `X`, `x`, `Accel_X`, `Accelerometer X`, `x_axis` and `X (g)` are all read as the X axis. Recognised timestamp headers include `Datetime`, `Time`, `timestamp`, `Date Time`, `local`, `utc` and `unixts`. If a required column cannot be matched, the file is skipped with a message listing the headers that were found.
* **If your file has more than one timestamp column**, which several devices export, the one used is chosen in this order: `Datetime` (and similar), then a local-time column, then a generic `time`/`timestamp`, then a UTC column, then a numeric Unix epoch column. You can select which one you want to be used by removing the extra columns before running (or rename the chosen column to "Datetime").

## Output

The tool generates one csv per run (including all participants), `<output folder>/<method>_wear_times.csv`, with columns
`studyid, WearTimeStart, WearTimeEnd`. There is one row per wear bout detected, meaning that each participant likely has more than one row of data.

## Method validation

All counts are extracted using agcounts.extract.get_counts. The Ahmadi method implementation was tested against the original R code as described in Letts et al. 2025, *Beyond the (Log)book:
Comparing Accelerometer Nonwear Detection Techniques in Toddlers*
([doi.org/10.1111/cch.70133](https://doi.org/10.1111/cch.70133)). This implementation of the Troiano method has not been formally tested against the ActiLife implementation. We recommend validating this implementation against ActiLife before relying on estimates.

**Note that the consecutive 0 count methods and Ahmadi method were tested with ActiGraph wGT3X-BT hip-worn devices recorded at 30Hz and used in the publication: Letts et al. 2025, *Beyond the (Log)book:
Comparing Accelerometer Nonwear Detection Techniques in Toddlers*
([doi.org/10.1111/cch.70133](https://doi.org/10.1111/cch.70133)). Accuracy on resampled data (and on data from other devices or wear locations) has not yet been established. We recommend validating on your own before relying on the estimates.**

## How do I download it?
To download Wear Aware, please follow these steps:

1. From the GitHub page, the most up-to-date version is located in the "Releases" section (right side panel).
2. Download the zip file for your operating system (Windows, macOS).
3. Once downloaded, unzip the files.
4. Double-click on "WearAware.app" (macOS) or "WearAware.exe" (Windows) to open the tool.
   
*Note.* On macOS, you may have to allow it by following these instructions: https://support.apple.com/en-ca/guide/mac-help/mh40617/mac. On Windows, you may have to select "More info" then "Run anyway" on the pop-up window.

## How do I use it?
Once you have downloaded and opened Wear Aware, you are ready to run your data:

1. Choose your input folder: Select "Browse" to open the file selector and choose the folder where your .gt3x or .csv files are located. Files should be named using the studyid/participant id (e.g., participant001.gt3x, participant001.csv).
2. Choose your output folder: Select "Browse" to open the file selector and choose the folder where you want the output file to be saved.
3. Choose sampling frequency: select the frequency your files were recorded at from the dropdown, or type it in; any value is accepted, not just the listed ones. Files not recorded at one of the frequencies accepted by agcounts are resampled to 30Hz.
4. Choose nonwear method: see the Nonwear algorithm options section above for details on each method.
5. Click on Run. A progress bar will appear to track progress of all files in the input folder. If you have missed a selection, it will prompt you to finish the selections before running. If an individual file cannot be read, it is skipped and listed at the end; the rest of the batch still runs.

![screenshot of Wear Aware](<WearAwareScreenshot.png>)


## Can anyone use Wear Aware?
Yes, anyone can use the Wear Aware tool provided that they agree to and follow the copyright and Terms of Use (See [LICENSE](https://github.com/LettsE/WearAware/blob/main/LICENSE)) and that they cite its use. 

## How do I cite using it?
Please reference any use of Wear Aware by providing the GitHub link. If you use any information from the paper, please cite it in full: Letts, Elyse, Sarah M. da Silva, Natascja Di Cristofaro, Sara King-Dowling, and Joyce Obeid. “Beyond the (Log)book: Comparing Accelerometer Nonwear Detection Techniques in Toddlers.” *Child: Care, Health and Development* 51, no. 4 (2025): e70133. https://doi.org/10.1111/cch.70133.




# How to run Wear Aware from the code

You will need python 3 installed on your machine, then install the requirements:

```
pip install -r requirements.txt
```

Then run main.py

```
python main.py
```

