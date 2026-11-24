"""Unified desktop hub for FRIDAY settings and companion panels."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
                               QScrollArea, QPushButton, QLabel, QFrame)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QGuiApplication


class SettingsWindow(QWidget):
    TABS = ('General', 'Chat', 'Media', 'Tasks', 'Vision', 'Shelf')
    KEYS = ('settings', 'chat', 'media', 'tasks', 'vision', 'shelf')

    def __init__(self, panels, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle('FRIDAY · Settings Hub')
        self.setMinimumSize(680, 460); self.resize(740, 520)
        self.panels = {key: panels[key] for key in self.KEYS}
        for key, panel in self.panels.items(): setattr(self, key, panel)
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.sidebar = QFrame(); self.sidebar.setObjectName('settingsSidebar'); self.sidebar.setFixedWidth(178)
        nav = QVBoxLayout(self.sidebar); nav.setContentsMargins(14, 24, 14, 16); nav.setSpacing(6)
        brand = QLabel('FRIDAY'); brand.setObjectName('hubBrand'); nav.addWidget(brand)
        nav.addSpacing(12)
        self.buttons = []; self.stack = QStackedWidget(); self.stack.setObjectName('settingsStack')
        self.scroll_areas = {}
        for i, (label, key) in enumerate(zip(self.TABS, self.KEYS)):
            button = QPushButton(label); button.setCheckable(True); button.setObjectName('navButton')
            button.setIcon(self._nav_icon(i)); button.setIconSize(QSize(18, 18))
            button.clicked.connect(lambda checked=False, index=i: self.select_tab(index))
            nav.addWidget(button); self.buttons.append(button)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(self.panels[key]); self.stack.addWidget(scroll); self.scroll_areas[key] = scroll
        nav.addStretch(1)
        footer = QLabel('Your assistant, at a glance'); footer.setObjectName('hubFooter'); nav.addWidget(footer)
        root.addWidget(self.sidebar); root.addWidget(self.stack, 1)
        self.select_tab(0)
        self.set_theme('dark')

    @staticmethod
    def _nav_icon(index):
        image = QPixmap(24, 24); image.fill(Qt.transparent)
        p = QPainter(image); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#8B919E'), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if index == 0:  # sliders / general
            for y, x in ((6, 8), (12, 15), (18, 10)):
                p.drawLine(4, y, 20, y); p.drawEllipse(x-2, y-2, 4, 4)
        elif index == 1:  # chat bubble
            p.drawRoundedRect(3, 4, 18, 14, 4, 4); p.drawLine(8, 18, 6, 21); p.drawLine(6, 21, 11, 18)
        elif index == 2:  # media
            p.drawRoundedRect(3, 4, 18, 16, 4, 4); p.drawLine(10, 8, 10, 16); p.drawLine(10, 8, 16, 12); p.drawLine(16, 12, 10, 16)
        elif index == 3:  # tasks
            for y in (6, 12, 18):
                p.drawRect(4, y-2, 4, 4); p.drawLine(11, y, 20, y)
        elif index == 4:  # vision / eye
            p.drawArc(3, 6, 18, 12, 0, 180*16); p.drawArc(3, 6, 18, 12, 180*16, 180*16); p.drawEllipse(10, 10, 4, 4)
        else:  # shelf / archive
            p.drawRoundedRect(3, 6, 18, 14, 3, 3); p.drawLine(3, 10, 21, 10); p.drawRoundedRect(9, 12, 6, 3, 1, 1)
        p.end(); return QIcon(image)

    @property
    def current_tab(self): return self.TABS[self.stack.currentIndex()]

    def select_tab(self, tab):
        if isinstance(tab, str):
            if tab not in self.TABS: raise ValueError(tab)
            index = self.TABS.index(tab)
        else: index = max(0, min(len(self.TABS)-1, int(tab)))
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.buttons):
            button.setChecked(i == index); button.setProperty('active', i == index)
            button.style().unpolish(button); button.style().polish(button)
        if self.KEYS[index] == 'tasks': self.tasks.refresh()
        return self.TABS[index]

    def open_tab(self, tab):
        self.select_tab(tab)
        self.show()
        if QGuiApplication.platformName() != 'offscreen':
            self.raise_()
            self.activateWindow()

    def set_theme(self, theme):
        from pathlib import Path
        qss = Path(__file__).resolve().parent.parent / 'themes' / f'{theme}.qss'
        self.setStyleSheet(qss.read_text(encoding='utf-8') + '''
            #settingsSidebar { background: rgba(128,132,146,0.08); border-right: 1px solid rgba(128,132,146,0.20); }
            #hubBrand { font-size: 17pt; font-weight: 700; letter-spacing: 2px; padding: 4px 10px; }
            #hubFooter { font-size: 8pt; opacity: .62; padding: 8px; }
            QPushButton#navButton { text-align: left; border: 0; border-radius: 9px; padding: 9px 12px; background: transparent; }
            QPushButton#navButton:hover { background: rgba(128,132,146,0.13); }
            QPushButton#navButton:checked { background: rgba(88,132,255,0.20); color: #7EA5FF; font-weight: 600; }
            QScrollArea { background: transparent; }
        ''')

    def closeEvent(self, event):
        event.ignore(); self.hide()
