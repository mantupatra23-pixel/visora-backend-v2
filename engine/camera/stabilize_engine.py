# engine/camera/stabilize_engine.py
import cv2
import numpy as np
import os
import uuid
from moviepy.editor import VideoFileClip, VideoFileClip

def stabilize_video(input_path, smoothing_radius=30):
    """
    Simple vid stabilization using OpenCV feature transform chain and smoothing.
    """
    cap = cv2.VideoCapture(input_path)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    # Read first frame
    _, prev = cap.read()
    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    transforms = np.zeros((n_frames-1, 3), np.float32)

    for i in range(n_frames-1):
        success, curr = cap.read()
        if not success:
            break
        curr_gray = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
        # feature detection
        prev_pts = cv2.goodFeaturesToTrack(prev_gray,
                                           maxCorners=200,
                                           qualityLevel=0.01,
                                           minDistance=30,
                                           blockSize=3)
        curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, prev_pts, None)
        # filter valid points
        valid = status.flatten()==1
        prev_pts = prev_pts[valid]
        curr_pts = curr_pts[valid]
        # estimate transform
        m, inliers = cv2.estimateAffinePartial2D(prev_pts, curr_pts)
        if m is None:
            m = np.eye(2,3)
        dx = m[0,2]
        dy = m[1,2]
        da = np.arctan2(m[1,0], m[0,0])
        transforms[i] = [dx,dy,da]
        prev_gray = curr_gray

    # accumulate transforms
    trajectory = np.cumsum(transforms, axis=0)
    # smooth trajectory
    def smooth(trajectory, radius):
        smoothed = np.copy(trajectory)
        for i in range(3):
            smoothed[:,i] = np.convolve(trajectory[:,i], np.ones(radius)/radius, mode='same')
        return smoothed
    smoothed = smooth(trajectory, smoothing_radius)
    diff = smoothed - trajectory
    transforms_smooth = transforms + diff

    # apply transforms to frames and write
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    out_path = f"static/videos/stabilized_{uuid.uuid4().hex[:8]}.mp4"
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter('/tmp/tmp_out.mp4', fourcc, fps, (w,h))
    _, frame = cap.read()
    frame_idx = 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx < n_frames-1:
            dx = transforms_smooth[frame_idx,0]
            dy = transforms_smooth[frame_idx,1]
            da = transforms_smooth[frame_idx,2]
            m = np.array([[np.cos(da), -np.sin(da), dx],
                          [np.sin(da),  np.cos(da), dy]])
        else:
            m = np.eye(2,3)
        stabilized = cv2.warpAffine(frame, m, (w,h))
        out.write(stabilized)
        frame_idx += 1
    out.release()
    # convert to web-friendly mp4 using ffmpeg (moviepy or os.system)
    os.system(f"ffmpeg -y -i /tmp/tmp_out.mp4 -c:v libx264 -preset fast -pix_fmt yuv420p {out_path}")
    os.remove('/tmp/tmp_out.mp4')
    return out_path
