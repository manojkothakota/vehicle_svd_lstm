import pypandoc

Secure Platoon Communication in a Software-Defined Vehicle (SDV)

A Python-based testbed for studying secure vehicle platooning using an AUTOSAR-inspired ECU architecture, MQTT-based communication, layered message validation, and Signal Temporal Logic (STL) runtime monitoring.

## Overview

Vehicle platoons rely on continuous communication between vehicles. False or replayed messages can disrupt vehicle coordination and create safety risks. This project models a 10-vehicle platoon and explores how multiple security and safety mechanisms can work together to detect suspicious messages and reduce their effect on vehicle control.

Each simulated vehicle is represented by four independent nodes:

- **Sensor MCU** — generates vehicle-state information such as position, speed, and heading.
- **Powertrain MCU** — validates actuation frames and simulates vehicle motion.
- **High-Performance Computer (HPC)** — runs platoon control, plausibility detection, a braking safety shield, and update-management functions.
- **Gateway ECU** — serves as the controlled interface between the vehicle and external networks.

The architecture reflects AUTOSAR Classic roles for the MCU nodes and an AUTOSAR Adaptive role for the HPC. It is an architectural emulation, not a certified vendor AUTOSAR implementation.

## Key Features

- Simulates a **10-vehicle platoon** with 40 vehicle nodes.
- Uses **Python**, **Eclipse Mosquitto**, and **Paho MQTT** for message exchange.
- Models four logical communication networks:
  - In-vehicle communication (CAN-like, simulated using MQTT)
  - Vehicle-to-Vehicle (V2V)
  - Vehicle-to-Cloud (V2Cloud)
  - HPC-to-AI service communication
- Implements layered message protection and validation concepts:
  - CRC-8 and an alive counter for actuation-frame checks
  - HMAC-based message authentication
  - Replay-counter and platoon-membership checks
  - Kinematic plausibility detection for received vehicle-state messages
  - Rule-based REAL/FAKE command classification
  - An integrated LSTM-based authenticity path
  - Range validation for OTA update parameters
- Uses a braking-distance safety shield to override commands when collision risk is detected.
- Monitors safety and performance requirements using **Signal Temporal Logic (STL)** and quantitative robustness values.
- Provides a browser-based operator view and passive network analysis in the described testbed.

## Architecture

```text
                         Cloud / Operator UI
                                  |
                              MQTT backbone
                                  |
              +-------------------+-------------------+
              |                   |                   |
           Vehicle 1            Vehicle 2           Vehicle N
              |                   |                   |
       +------+-------+     +-----+--------+    +-----+--------+
       |              |     |              |    |              |
   Gateway ECU      HPC  Gateway ECU      HPC  Gateway ECU      HPC
       |              |     |              |    |              |
       +--------------+     +--------------+    +--------------+
              |
       +------+----------------------+
       |                             |
   Sensor MCU                 Powertrain MCU
   (Classic-style)            (Classic-style)

HPC: Adaptive-style application role
Gateway: controlled external communication boundary
MQTT: shared testbed transport for the simulated logical networks
