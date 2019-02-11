import numpy as np
from scipy import signal
import math
from collections import Counter
from scipy import stats
from scipy.signal import argrelextrema
import ujson
import os
import math
dir_path = os.path.dirname(os.path.realpath(__file__))

visualize = True
debug = False

if visualize == True:
    import matplotlib.pyplot as plt


def triangulate_centroid(readings, circles=[[-1, 0], [1, 0], [0, -math.sqrt(3)]]):
	"""
	Given a 1x3 array of readings, and 3x2 array of circles
	Returns the weighted centroid (1x2)
	"""
	return np.divide(np.dot(readings, circles), .00001+np.sum(readings))


def shift_mean(data, ts_col, feature_cols):
    return np.hstack([
        data[:, ts_col],
        data[:, feature_cols] - np.mean(data[:, feature_cols], axis=0)
    ])


def swing_count_svc(data, ts_col, feature_cols):
    def clustering_dedup(maximas):  # maximas: timestamps
        # merge within window until no change
        # 2-level clustering (dedup then detect, respectively)
        idx_min = maximas.min()
        idx_max = maximas.max()

        seg_span = 1700  # millisecond

        segs = np.array(maximas).reshape(-1, 1)
        if debug == True:
            print(len(segs))
        while True:
            curr_seg_idx = 0
            epoch_updated = False
            while curr_seg_idx < len(segs) - 1:
                curr_seg = segs[curr_seg_idx]
                next_seg = segs[curr_seg_idx+1]
                curr_mean = np.mean(data[curr_seg, 0])
                next_mean = np.mean(data[next_seg, 0])
                if next_mean - curr_mean < seg_span:
                    segs = [*segs[:curr_seg_idx],
                            [*curr_seg, *next_seg], *segs[curr_seg_idx+2:]]
                    epoch_updated = True
                curr_seg_idx += 1
            if debug == True:
                print(len(segs))
            if epoch_updated == False:
                break
        if debug == True:
            print("!!!")
        return segs

    data = shift_mean(data, ts_col, feature_cols).copy()
    polls = []
    for i in range(0, len(feature_cols)):
        maxima_idx = np.array(argrelextrema(data[:, i+1], np.greater))[0]
        threshold = data[:, i+1].std()*0.8
        maxima_idx_filtered = [
            m_i for m_i in maxima_idx if data[m_i, i+1] > threshold]
        ts_dedup = clustering_dedup(data[maxima_idx_filtered, 0])
        polls.append(len(ts_dedup))  # silly polling for now
    return math.trunc(np.average(polls))


def hit_detection_svc(data, ts_col, feature_cols):
    data = data[:, [ts_col, *feature_cols]].copy()
    result = np.empty((len(data), 2), dtype=float)
    for i, entry in enumerate(data):
        if np.sum(entry[1:]) > 0:
            result[i] = triangulate_centroid(entry[1:])
        else:
            result[i] = [0, 0]
    return np.hstack([data[:, 0], result])


def fft_svc(data, ts_col, feature_cols, win_len=10): # win_len=0 for unwindowed fft, unit: sec
    def fft(y_temp, topk=.1, sample_rate=0.005):  # 5e-3s, 5ms):
        """
        Given a feature vector (1-D) sorted in time domain, this function performs a Fast DFT (real part only) and returns:
        x_freq: frequencies in frequency domain
        y_freq_abs | y_freq_abs_filtered: amplitude in frequency domain 

        Arguments:
        y_temp: feature vector in time domain
        topk: \in (0,1) | [1, len(y_temp)]; returns the top frequencies & amplitudes by top percentage or count
        """
        # y_temp -= np.mean(y_temp)
        y_freq = np.fft.rfft(y_temp)
        y_freq_abs = np.abs(y_freq)
        x_freq = np.fft.rfftfreq(len(y_temp), d=sample_rate)
        if topk != None:
            topk = int(topk*len(y_freq)) if topk < 1 else topk
            y_freq_idx_by_amp = np.argsort(y_freq_abs)[::-1][:topk]
            y_freq_mask = np.ones(len(y_freq), dtype=bool)
            y_freq_mask[y_freq_idx_by_amp] = False
            y_freq_filtered = y_freq.copy()
            y_freq_filtered[y_freq_mask] = 0
            y_freq_abs_filtered = np.abs(y_freq_filtered)
            # return x_freq, y_freq_abs_filtered/y_freq_abs_filtered.max()
            return x_freq, y_freq_abs_filtered
        else:
            return x_freq, y_freq_abs

    # swing_period = (1.5, 3)

    # must treat timestamps as evenly distributed
    data = shift_mean(data, ts_col, feature_cols).copy()
    ts_col = data[:, 0].copy()
    mpu_time_min = data[:, 0].min()
    mpu_time_max = data[:, 0].max()
    data[:, 0] -= mpu_time_min
    data[:, 0] = np.linspace(0, mpu_time_max - mpu_time_min, len(data))

    sample_rate = (mpu_time_max - mpu_time_min)/len(data)

    if win_len > 0:
        n_window = math.floor((mpu_time_max - mpu_time_min)/win_len)
        n_winlen = math.floor(data.shape[0]/n_window)
        if n_winlen == 0:
            return None, None
    else:
        n_window = 1
        n_winlen = data.shape[0]

    n_feature = len(feature_cols)

    fft_freqs = np.fft.rfftfreq(n_winlen, d=sample_rate)
    fft_topk = 20
    fft_result = np.ndarray((n_feature, n_window, len(fft_freqs)))
    swing_frequency = np.ndarray((n_feature, n_window))
    for i in range(0, n_feature):
        if debug:
            print(i, "-th feature stats", stats.describe(data[:, i+1]))

        if visualize:
            plt.figure(str(i) + '-th data')
            plt.plot(data[:, 0], data[:, i+1])
            plt.figure(str(i) + '-th spectrum (A-T)')

        for j in range(0, n_window):
            x, y = fft(data[n_winlen*j:n_winlen*(j+1), i+1],
                       topk=fft_topk, sample_rate=sample_rate)
            fft_result[i, j, :] = y.copy()

            if debug:
                print('Swing period for feature {0} window {1}: {2}'.format(
                    i, j, 1/x[np.argmax(y)]))

            if visualize:
                plt.scatter(1/x, y, label=n_winlen*j +
                            i, alpha=.5, s=100*y/max(y))

            swing_frequency[i, j] = 1/x[np.argmax(y)]
    
    if win_len > 0:
        return fft_result, swing_frequency
    else:
        return fft_result[:, 0, :], swing_frequency[:, 0]