#!/usr/bin/env python3
"""Запуск iLab Free.   py run.py          — робота з реєстратором
                       py run.py --demo   — демо-режим без плати"""
import sys

from ilab.gui import App

if __name__ == "__main__":
    App(demo="--demo" in sys.argv).mainloop()
