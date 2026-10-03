import sys
from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QLineEdit, QPushButton, QRadioButton, QComboBox, QFileDialog, QProgressBar, QButtonGroup, QGridLayout, QMessageBox, QToolTip, QHBoxLayout, QVBoxLayout
from PyQt5.QtGui import QMovie, QDoubleValidator, QFontDatabase
from PyQt5.QtCore import QSize, Qt
import os
from utils.loader import load_raw_accel_file, is_supported_input
from utils.resampler import detect_sampling_rate
from utils.nonwear_methods import run_method, METHOD_LABELS
from os import path
import multiprocessing
import traceback

# Text is shown this many points larger than the platform's default font size.
# DEFAULT_FONT_POINT_SIZE is used only if the platform reports its default in
# pixels rather than points.
FONT_SIZE_INCREASE = 5
DEFAULT_FONT_POINT_SIZE = 12

# How far the radio-button options are indented under their question, in pixels.
RADIO_INDENT = 24

# Extra vertical space added before each question, in pixels. It is applied as
# an empty layout row above the question, so the gap between a question and its
# options is left as it was.
QUESTION_SPACING = 12

# Common sampling rates, offered as a dropdown for convenience. The field is
# editable, so any frequency can be typed instead.
SAMPLING_RATE_PRESETS = [
    "12.5", "20", "25", "30", "32", "40", "50", "60", "64", "80", "90", "100", "128", "200", "256",
]
DEFAULT_SAMPLING_RATE = "30"

# Accepted range for a typed frequency.
MIN_SAMPLING_RATE = 0.01
MAX_SAMPLING_RATE = 10000.0

# Warn when the rate inferred from the file's timestamps disagrees with the
# rate the user selected by more than this fraction.
SAMPLING_RATE_TOLERANCE = 0.05

METHOD_TOOLTIP = (
    "<html><body style='max-width:450px'>"
    "Choose which nonwear detection method to apply. See README and "
    "doi.org/10.1111/cch.70133 for details on which to choose."
    "</body></html>"
)


def get_relative_path(relpath):
    return path.abspath(path.join(path.dirname(__file__), relpath))


def list_input_files(input_folder):
    """The files a run will process, in the order it will process them.

    Only .gt3x and .csv files are included. Shared by the pre-run frequency
    check and the worker, so both always agree on what the batch contains.
    """
    files = []
    for filename in sorted(os.listdir(input_folder)):
        file_path = os.path.join(input_folder, filename)
        if not os.path.isfile(file_path) or not is_supported_input(filename):
            continue
        files.append((filename, file_path))
    return files


class NonwearApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        # Main layout
        main_layout = QGridLayout()

        # Description
        doc_label = QLabel(
            "For full documentation and user instructions, please click <a href='https://github.com/LettsE/WearAware'>here</a>."
        )
        doc_label.setWordWrap(True)
        doc_label.setAlignment(Qt.AlignCenter)

        # Input Folder
        input_info = QLabel("ℹ️")
        input_info.setToolTip(
            "<html><body style='max-width:450px'>"
            "Click 'Browse' to select the folder where your input files are located. "
            "Both .gt3x and .csv files are read. "
            "Files should be named using the studyid/participant id (e.g. participant001.csv)."
            "</body></html>"
        )
        input_info.mousePressEvent = lambda event: QToolTip.showText(event.globalPos(), input_info.toolTip())
        input_label = QLabel("Input folder:")
        self.input_line_edit = QLineEdit()
        input_button = QPushButton("Browse folders")
        input_button.clicked.connect(self.browseInputFolder)

        # Output Folder
        output_info = QLabel("ℹ️")
        output_info.setToolTip(
            "<html><body style='max-width:450px'>"
            "Click 'Browse' to select the folder where the output CSV will be saved."
            "</body></html>"
        )
        output_info.mousePressEvent = lambda event: QToolTip.showText(event.globalPos(), output_info.toolTip())
        output_label = QLabel("Output folder:")
        self.output_line_edit = QLineEdit()
        output_button = QPushButton("Browse folders")
        output_button.clicked.connect(self.browseOutputFolder)

        # Sampling frequency of the input files
        sampling_rate_info = QLabel("ℹ️")
        sampling_rate_info.setToolTip(
            "<html><body style='max-width:450px'>"
            "The sampling frequency your files were recorded at, in Hz. "
            "Pick one of the listed frequencies or type your own, for example 12.5 or 33.3. "
            "All files in the input folder must share this frequency."
            "</body></html>"
        )
        sampling_rate_info.mousePressEvent = lambda event: QToolTip.showText(event.globalPos(), sampling_rate_info.toolTip())
        sampling_rate_label = QLabel("Sampling frequency (Hz):")
        self.sampling_rate_combo = QComboBox()
        self.sampling_rate_combo.setEditable(True)
        self.sampling_rate_combo.setInsertPolicy(QComboBox.NoInsert)
        self.sampling_rate_combo.addItems(SAMPLING_RATE_PRESETS)
        self.sampling_rate_combo.setValidator(
            QDoubleValidator(MIN_SAMPLING_RATE, MAX_SAMPLING_RATE, 4, self)
        )
        self.sampling_rate_combo.setCurrentText(DEFAULT_SAMPLING_RATE)

        # Nonwear detection method
        method_info = QLabel("ℹ️")
        method_info.setToolTip(METHOD_TOOLTIP)
        method_info.mousePressEvent = lambda event: QToolTip.showText(event.globalPos(), method_info.toolTip())
        method_label = QLabel("Which nonwear detection method would you like to use?")

        self.method_5min_rb = QRadioButton("5min_0count")
        self.method_10min_rb = QRadioButton("10min_0count")
        self.method_20min_rb = QRadioButton("20min_0count")
        self.method_30min_rb = QRadioButton("30min_0count")
        self.method_60min_rb = QRadioButton("60min_0count")
        self.method_troiano_rb = QRadioButton("Troiano60s")
        self.method_ahmadi_rb = QRadioButton("Ahmadi")

        self.method_buttons = {
            "5min_0count": self.method_5min_rb,
            "10min_0count": self.method_10min_rb,
            "20min_0count": self.method_20min_rb,
            "30min_0count": self.method_30min_rb,
            "60min_0count": self.method_60min_rb,
            "troiano60s": self.method_troiano_rb,
            "ahmadi": self.method_ahmadi_rb,
        }

        method_group = QButtonGroup(self)
        for button in self.method_buttons.values():
            method_group.addButton(button)

        # Run button
        run_button = QPushButton("Run nonwear detection")
        run_button.clicked.connect(self.runNonwearDetection)

        # Status label
        self.status_label = QLabel()
        self.status_label.setVisible(False)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        # Running GIF
        self.running_gif_label = QLabel()
        movie = QMovie(get_relative_path("assets/RunningGif08March2025.gif"))
        gif_size = QSize(225, 155)
        movie.setScaledSize(gif_size)
        self.running_gif_label.setMovie(movie)
        # Reserve the GIF's full height in the layout. Without a minimum size the
        # layout squeezes the label shorter than the GIF once it is shown, and the
        # top and bottom of the animation are clipped.
        self.running_gif_label.setMinimumSize(gif_size)
        self.running_gif_label.setVisible(False)
        self.running_gif_label.setAlignment(Qt.AlignCenter)

        # Footer
        footer_layout = QHBoxLayout()
        footer_layout.setAlignment(Qt.AlignCenter)
        copyright_label = QLabel(
            "Copyright © 2026 McMaster University, Hamilton, Ontario, Canada. "
            "Wear Aware, authored by Elyse Letts and Joyce Obeid, "
            "is the copyright of McMaster University. By using Wear Aware you agree to the <a href='https://github.com/LettsE/WearAware/blob/main/LICENSE'>Terms of Use</a>."
        )
        copyright_label.setWordWrap(True)
        copyright_label.setFixedWidth(850)
        # The copyright statement stays at the platform's default font size
        # rather than the enlarged application font.
        # The size is set explicitly: a font that leaves it unset would inherit
        # the enlarged size from the parent widget.
        default_font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        default_font.setPointSize(
            default_font.pointSize() if default_font.pointSize() > 0 else DEFAULT_FONT_POINT_SIZE
        )
        copyright_label.setFont(default_font)
        footer_layout.addWidget(copyright_label)

        # Main layout setup
        main_layout.addWidget(doc_label, 0, 0, 1, 4)

        main_layout.addWidget(input_info, 1, 0)
        main_layout.addWidget(input_label, 1, 1)
        main_layout.addWidget(self.input_line_edit, 1, 2)
        main_layout.addWidget(input_button, 1, 3)

        main_layout.setRowMinimumHeight(2, QUESTION_SPACING)
        main_layout.addWidget(output_info, 3, 0)
        main_layout.addWidget(output_label, 3, 1)
        main_layout.addWidget(self.output_line_edit, 3, 2)
        main_layout.addWidget(output_button, 3, 3)

        main_layout.setRowMinimumHeight(4, QUESTION_SPACING)
        main_layout.addWidget(sampling_rate_info, 5, 0)
        main_layout.addWidget(sampling_rate_label, 5, 1)
        main_layout.addWidget(self.sampling_rate_combo, 5, 2)

        main_layout.setRowMinimumHeight(6, QUESTION_SPACING)
        main_layout.addWidget(method_info, 7, 0)
        main_layout.addWidget(method_label, 7, 1, 1, 3)
        main_layout.addLayout(self.indentedGroup(self.method_buttons.values()), 8, 1, 7, 3)

        main_layout.addWidget(self.running_gif_label, 15, 0, 1, 4)
        main_layout.addWidget(self.status_label, 16, 0, 1, 4)
        main_layout.addWidget(self.progress_bar, 17, 0, 1, 4)
        main_layout.addWidget(run_button, 18, 3)

        # Set layout
        container_layout = QVBoxLayout()
        container_layout.addLayout(main_layout)
        container_layout.addLayout(footer_layout)

        self.setLayout(container_layout)
        self.setWindowTitle('Wear Aware ©')
        self.show()

    @staticmethod
    def indentedGroup(buttons):
        """The radio buttons stacked in a layout, indented under their question."""
        layout = QVBoxLayout()
        layout.setContentsMargins(RADIO_INDENT, 0, 0, 0)
        for button in buttons:
            layout.addWidget(button)
        return layout

    def browseInputFolder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder")
        if folder:
            self.input_line_edit.setText(folder)

    def browseOutputFolder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_line_edit.setText(folder)

    def selectedMethodKey(self):
        for key, button in self.method_buttons.items():
            if button.isChecked():
                return key
        return None

    def getSelectedData(self, sampling_rate):
        return {
            "input_folder": self.input_line_edit.text(),
            "output_folder": self.output_line_edit.text(),
            "method": self.selectedMethodKey(),
            "sampling_rate": sampling_rate,
        }

    def parseSamplingRate(self):
        """The typed sampling frequency as a number, or None if unusable."""
        text = self.sampling_rate_combo.currentText().strip()
        try:
            value = float(text)
        except (TypeError, ValueError):
            value = None

        if value is None or not (MIN_SAMPLING_RATE <= value <= MAX_SAMPLING_RATE):
            QMessageBox.warning(
                self,
                "Sampling frequency",
                "'%s' is not a usable sampling frequency.\n\nEnter the number of "
                "samples per second your files were recorded at -- for example "
                "12.5, 30 or 100. Values between %g and %g Hz are accepted."
                % (text, MIN_SAMPLING_RATE, MAX_SAMPLING_RATE),
            )
            return None
        return value

    def runNonwearDetection(self):
        if not self.areAllSelectionsMade():
            QMessageBox.warning(self, "Missing Selection", "Missing one or more selections")
            return

        sampling_rate = self.parseSamplingRate()
        if sampling_rate is None:
            return

        # Start the GIF and show the progress bar
        self.running_gif_label.setVisible(True)
        self.status_label.setVisible(True)
        self.running_gif_label.movie().start()
        self.progress_bar.setVisible(True)

        multiprocessing.freeze_support()

        self.thread = QtCore.QThread(self)
        self.thread.setStackSize(64 * 1024 * 1024)
        self.worker = Worker(self.getSelectedData(sampling_rate))
        self.worker.moveToThread(self.thread)
        self.worker.finished.connect(self.onPipelineFinished)
        self.worker.progressChanged.connect(self.onPipelineProgressed)
        self.worker.filesFailed.connect(self.onFilesFailed)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def onPipelineProgressed(self, count, status):
        print("Progress update", count)
        self.progress_bar.setValue(count)
        self.status_label.setText(status)

    def onFilesFailed(self, failures):
        details = "\n\n".join("%s:\n%s" % (name, message) for name, message in failures)
        QMessageBox.warning(
            self,
            "Some files were skipped",
            "%d file(s) could not be processed. Everything else finished normally.\n\n%s"
            % (len(failures), details),
        )

    def onPipelineFinished(self):
        self.running_gif_label.setVisible(False)
        self.progress_bar.setVisible(False)

    def areAllSelectionsMade(self):
        return bool(
            self.input_line_edit.text()
            and self.output_line_edit.text()
            and self.selectedMethodKey()
        )


class Worker(QtCore.QObject):
    progressChanged = QtCore.pyqtSignal(int, str)
    filesFailed = QtCore.pyqtSignal(list)
    finished = QtCore.pyqtSignal()

    def __init__(self, selectedData):
        super().__init__()
        self.selectedData = selectedData

    def run(self):
        method_key = self.selectedData["method"]
        if method_key not in METHOD_LABELS:
            self.progressChanged.emit(100, "Error: No nonwear detection method selected!")
            self.finished.emit()
            return

        input_folder = self.selectedData['input_folder']
        output_folder = self.selectedData['output_folder']
        sampling_rate = self.selectedData['sampling_rate']

        files = list_input_files(input_folder)

        if not files:
            self.progressChanged.emit(100, "No .gt3x or .csv files found in the input folder.")
            self.finished.emit()
            return

        output_csv_path = os.path.join(output_folder, "%s_wear_times.csv" % method_key)
        failures = []

        for idx, (filename, file_path) in enumerate(files):
            progress = round(idx / len(files) * 100)
            studyid = os.path.splitext(filename)[0]

            try:
                self.progressChanged.emit(progress, "Reading " + filename + "...")
                data = load_raw_accel_file(file_path)

                # The selected rate is authoritative, but scoring a file at the
                # wrong rate produces plausible-looking nonsense rather than an
                # error, so a disagreement with the timestamps is surfaced.
                detected_hz = detect_sampling_rate(data)
                if detected_hz and abs(detected_hz - sampling_rate) / sampling_rate > SAMPLING_RATE_TOLERANCE:
                    warning = ("WARNING: %s looks like %.2f Hz data but %g Hz was selected; "
                               "using the selected rate." % (filename, detected_hz, sampling_rate))
                    print(warning)
                    self.progressChanged.emit(progress, warning)

                self.progressChanged.emit(progress, "Detecting nonwear for " + filename + " (" + METHOD_LABELS[method_key] + ")...")
                wear_bouts = run_method(method_key, data, sampling_rate)

                wear_bouts = wear_bouts.copy()
                wear_bouts.insert(0, "studyid", studyid)
                wear_bouts.to_csv(
                    output_csv_path, mode='a', index=False,
                    header=not os.path.exists(output_csv_path),
                )

                self.progressChanged.emit(
                    round((idx + 1.0) / len(files) * 100),
                    "File " + filename + " successfully processed! (%d wear bout(s))" % len(wear_bouts),
                )

            except Exception as error:
                # One unreadable/unsupported file should not abandon the rest of the batch.
                failures.append((filename, str(error)))
                traceback.print_exc()
                self.progressChanged.emit(round((idx + 1.0) / len(files) * 100),
                                          "Skipped " + filename + ": " + str(error))

        if failures:
            self.filesFailed.emit(failures)
            self.progressChanged.emit(100, "Finished with %d of %d file(s) skipped." % (len(failures), len(files)))
            self.finished.emit()
            return

        self.progressChanged.emit(100, "Complete! Wrote " + output_csv_path)
        self.finished.emit()


def main():
    app = QApplication(sys.argv)
    font = app.font()
    default_size = font.pointSize() if font.pointSize() > 0 else DEFAULT_FONT_POINT_SIZE
    font.setPointSize(default_size + FONT_SIZE_INCREASE)
    app.setFont(font)
    ex = NonwearApp()
    sys.exit(app.exec_())


if __name__ == '__main__':
    multiprocessing.freeze_support()
    multiprocessing.set_start_method('spawn', force=True)
    main()
