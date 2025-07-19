#!/usr/bin/env python3

import serial
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame
import sys
import time
import argparse
from datetime import datetime

# Serial configuration
parser = argparse.ArgumentParser(description="K5 Screen Viewer")
parser.add_argument(
    "-port", dest="serial_port", default="/dev/ttyUSB0",
    help="Serial port to use (default: /dev/ttyUSB0)"
)
args = parser.parse_args()

SERIAL_PORT = args.serial_port
BAUDRATE = 38400
TIMEOUT = 0.5

# Screen configuration
WIDTH, HEIGHT = 128, 64
FRAME_SIZE = 1024

# Protocol
HEADER = b'\xAA\x55'
TYPE_SCREENSHOT = b'\x01'
TYPE_DIFF = b'\x02'

# Framebuffer
framebuffer = bytearray([0] * FRAME_SIZE)

def read_frame(ser):
    global framebuffer
    while True:
        b = ser.read(1)
        if not b:
            return None
        if b == HEADER[0:1]:
            b2 = ser.read(1)
            if b2 == HEADER[1:2]:
                t = ser.read(1)
                size_bytes = ser.read(2)
                size = int.from_bytes(size_bytes, 'big')
                if t == TYPE_SCREENSHOT and size == FRAME_SIZE:
                    payload = ser.read(FRAME_SIZE)
                    framebuffer = bytearray(payload)
                    return framebuffer
                elif t == TYPE_DIFF and size % 9 == 0:
                    payload = ser.read(size)
                    framebuffer = apply_diff(framebuffer, payload)
                    return framebuffer

def apply_diff(framebuffer, diff_payload):
    i = 0
    while i + 9 <= len(diff_payload):
        block_index = diff_payload[i]
        i += 1
        if block_index >= 128:
            break
        framebuffer[block_index * 8 : block_index * 8 + 8] = diff_payload[i : i + 8]
        i += 8
    return framebuffer

def draw_frame(screen, framebuffer, bg_color=(202, 225, 255), fg_color=(0, 0, 0), pixel_size=4):
    screen.fill(bg_color)

    def get_bit(bit_idx):
        byte_idx = bit_idx // 8
        bit_pos = bit_idx % 8
        if byte_idx < len(framebuffer):
            return (framebuffer[byte_idx] >> bit_pos) & 0x01
        return 0

    bit_index = 0
    for y in range(64):
        for x in range(128):
            if get_bit(bit_index):
                px = x * (pixel_size - 1)
                py = y * pixel_size
                pygame.draw.rect(screen, fg_color, (px, py, pixel_size - 1, pixel_size))
            bit_index += 1

    pygame.display.flip()
    return pygame.display.get_surface().copy()

def main():
    pixel_size = 4
    lost_frame = 0

    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=TIMEOUT)
    except serial.SerialException as e:
        print(f"[!] Serial error: {e}")
        sys.exit(1)

    pygame.init()
    screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))
    base_title = "Quansheng K5 Viewer by F4HWN"
    pygame.display.set_caption(f"{base_title} – No data")

    fg_color = (0, 0, 0)
    bg_color = (202, 202, 202)
    last_surface = None
    frame_count = 0
    last_time = time.time()

    try:
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise KeyboardInterrupt
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_q:
                        raise KeyboardInterrupt
                    elif event.key == pygame.K_SPACE and last_surface:
                        filename = datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
                        pygame.image.save(last_surface, filename)
                        print(f"[✔] Screenshot saved: {filename}")
                    elif event.key == pygame.K_o:
                        bg_color = (255, 180, 100)
                    elif event.key == pygame.K_b:
                        bg_color = (24, 116, 205)
                    elif event.key == pygame.K_g:
                        bg_color = (202, 202, 202)
                    elif event.key == pygame.K_i:
                        if bg_color == (0, 0, 0):
                            bg_color, fg_color = fg_color, (0, 0, 0)
                        else:
                            bg_color, fg_color = (0, 0, 0), bg_color
                        draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size)
                    elif event.key == pygame.K_UP:
                        if pixel_size < 11:
                            pixel_size += 1
                        screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))
                        draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size)
                    elif event.key == pygame.K_DOWN:
                        if pixel_size > 2:
                            pixel_size -= 1
                        screen = pygame.display.set_mode((WIDTH * (pixel_size - 1), HEIGHT * pixel_size))
                        draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size)

            frame = read_frame(ser)
            if frame:
                last_surface = draw_frame(screen, framebuffer, bg_color, fg_color, pixel_size)
                frame_count += 1
                now = time.time()
                if now - last_time >= 1.0:
                    fps = frame_count / (now - last_time)
                    pygame.display.set_caption(f"{base_title} – FPS: {fps:.1f}")
                    frame_count = 0
                    last_time = now
                    lost_frame = 0
            elif 'None':
                lost_frame += 1
                if(lost_frame > 4):
                    pygame.display.set_caption(f"{base_title} – No data")


    except KeyboardInterrupt:
        print("\n[✔] Exiting")
        ser.close()
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    main()
