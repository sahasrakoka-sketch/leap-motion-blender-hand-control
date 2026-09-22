# Leap Motion Blender Hand Control

Real-time 3D hand tracking and object interaction in Blender using a Leap Motion Controller.

## Overview

This project connects a Leap Motion Controller to Blender to create a real-time 3D hand-interaction system.

Hand movements are captured using Leap Motion tracking, processed in Python, transmitted to Blender using UDP, and converted into cursor movement and 3D object interactions.

The project was developed as an interactive Blender-based expo project.

## Features

- Real-time Leap Motion hand tracking
- Independent left and right hand cursors
- 3D cursor movement
- X-axis: left / right
- Y-axis: forward / backward depth
- Z-axis: up / down
- Pinch-based object interaction
- Drag and move 3D objects
- Fist-based hand clutch
- Two-hand object scaling
- Two-hand object rotation
- Two-hand object movement
- Blender presentation UI
- EXPO startup and shutdown scripts

## System Architecture

```text
Leap Motion Controller
        ↓
Ultraleap Hyperion
        ↓
LeapC
        ↓
Python Leap Bindings
        ↓
tracker.py
        ↓
UDP : 5005
        ↓
receiver.py
        ↓
Blender
        ↓
3D Cursors + Object Interaction
