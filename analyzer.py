import cv2
import glob
import numpy as np
import time

print("[INFO] Analyzer started")

while True:
    files = sorted(glob.glob("buffer/*.jpg"))[-200:]

    sharpness = []
    brightness = []

    for f in files:
        img = cv2.imread(f, 0)
        if img is None:
            continue

        lap = cv2.Laplacian(img, cv2.CV_64F)
        sharpness.append(lap.var())

        brightness.append(np.mean(img))

    if sharpness:
        print(f"[ANALYSIS] sharpness={np.mean(sharpness):.1f} brightness={np.mean(brightness):.1f}")

    time.sleep(10)
