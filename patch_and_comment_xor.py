"""
IDA Pro Python Script: Universal XOR Decryption, Commenting & Reassembly Manager
Target: libanogs.so (ARM64)
Author: Antigravity Agent

Features:
1. Universal Auto-Key Brute-Forcer (0x00 - 0xFF) for arbitrary strings / stack bytes
2. Dynamic Full-Binary Scanner: Traces ARM64 registers (W2 key, W1 len, X0 buf) for all calls to sub_2E34C4
3. Native IDA Dockable Tab ("XOR Decrypt & Reassemble Results") with Qt Table
4. All 27 Vulkan Function Pointer Resolvers in ano_init_vulkan_function_pointers (sub_382768)
5. Unreal Engine Reflection strings (/Script/CoreUObject., Property, libUE4.so)
6. Unity/Mono (Key 0xB6 / 182 & Key 0x18 / 24) & Android System strings
7. Reassembles BL sub_2E34C4 calls into NOP (0x1F, 0x20, 0x03, 0xD5)
8. Sets comments with the original encrypted string ('enc: ...')
"""

import os
import sys
import re

import idaapi
import ida_bytes
import ida_funcs
import ida_kernwin
import idautils

                  
HAS_QT = False
try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    HAS_QT = True
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
        HAS_QT = True
    except ImportError:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
            HAS_QT = True
        except ImportError:
            HAS_QT = False

                                                   
ARM64_NOP = b"\x1f\x20\x03\xd5"
SUB_2E34C4_EA = 0x2E34C4

                                               
COMMON_KEYWORDS = [
    "vk", "gl", "egl", "mono", "unity", "script", "lib", "android",
    "create", "destroy", "get", "set", "device", "image", "buffer",
    "queue", "memory", "property", "object", "client", "packet",
    "http", "proc", "class", "method", "field", "event", "system",
    "error", "failed", "shader", "render", "texture", "surface", "data",
    "config", "view", "window", "fence", "barrier", "layout", "format"
]

def auto_detect_xor_key(raw_input):
    """
    Test all 256 XOR keys (0x00 - 0xFF) on arbitrary string or bytes,
    ranking candidates by readability, English letter/vowel distribution,
    and identifier heuristics.
    """
    if isinstance(raw_input, str):
        raw_bytes = raw_input.encode("latin-1")
    else:
        raw_bytes = bytes(raw_input)
        
    candidates = []
    
    for key in range(256):
        dec = bytes(b ^ key for b in raw_bytes)
        printable = sum(1 for b in dec if 32 <= b < 127)
        if printable < len(dec) * 0.75:
            continue
            
        try:
            s = dec.decode("ascii")
        except:
            continue
            
        score = 0
        score += int((printable / len(dec)) * 40)
        
                            
        alphanumeric = sum(1 for c in s if c.isalnum() or c in "_/.- :%?=&()[]")
        score += int((alphanumeric / len(dec)) * 30)
        
                
        vowels = sum(1 for c in s.lower() if c in "aeiou")
        if len(s) >= 4:
            v_ratio = vowels / len(s)
            if 0.15 <= v_ratio <= 0.60:
                score += 20
                
                         
        s_lower = s.lower()
        for kw in COMMON_KEYWORDS:
            if kw in s_lower:
                score += 30
                
                                                           
        if s.isupper() and len(s) > 4:
            score -= 15
            
                                      
        weird = sum(1 for c in s if c in "~`^{}|<>\\")
        score -= weird * 5
        
        if score > 45:
            candidates.append({
                "key": key,
                "hex_key": f"0x{key:02X}",
                "score": score,
                "decrypted": s
            })
            
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def decode_arm64_instruction(inst):
    """Decode basic ARM64 instructions relevant to call argument tracing"""
                               
    if (inst & 0x7F800000) == 0x52800000:
        rd = inst & 0x1F
        imm = (inst >> 5) & 0xFFFF
        hw = (inst >> 21) & 3
        return ('MOVZ', rd, imm << (hw * 16))
                      
    if (inst & 0x7F800000) == 0x12800000:
        rd = inst & 0x1F
        imm = (inst >> 5) & 0xFFFF
        hw = (inst >> 21) & 3
        return ('MOVN', rd, ~(imm << (hw * 16)) & 0xFFFFFFFF)
                                    
    if (inst & 0xFF800000) == 0x32000000:
        rd = inst & 0x1F
        imm = (inst >> 10) & 0x3F
        return ('ORR', rd, imm)
                             
    if (inst & 0xFFE0FFE0) == 0x2A0003E0:
        rd = inst & 0x1F
        rm = (inst >> 16) & 0x1F
        return ('MOV_REG', rd, rm)
                             
    if (inst & 0xFFE0FFE0) == 0xAA0003E0:
        rd = inst & 0x1F
        rm = (inst >> 16) & 0x1F
        return ('MOV_REG', rd, rm)
                   
    if (inst & 0x9F000000) == 0x90000000:
        rd = inst & 0x1F
        immlo = (inst >> 29) & 3
        immhi = (inst >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20): imm -= (1 << 21)
        return ('ADRP', rd, imm)
                                   
    if (inst & 0xFF800000) == 0x91000000:
        rd = inst & 0x1F
        rn = (inst >> 5) & 0x1F
        imm = (inst >> 10) & 0xFFF
        return ('ADD_IMM', rd, rn, imm)
                                      
    if (inst & 0xFFC00000) == 0xB9400000:
        rt = inst & 0x1F
        rn = (inst >> 5) & 0x1F
        imm = ((inst >> 10) & 0xFFF) << 2
        return ('LDR_W', rt, rn, imm)
                                      
    if (inst & 0xFFC00000) == 0xB9000000:
        rt = inst & 0x1F
        rn = (inst >> 5) & 0x1F
        imm = ((inst >> 10) & 0xFFF) << 2
        return ('STR_W', rt, rn, imm)
    return ('OTHER', inst)


def trace_call_arguments(xref_ea, num_insts=32):
    """
    Trace arguments for a 'BL sub_2E34C4' call at xref_ea:
    Returns (key, length, buffer_ea, details)
    """
    raw = ida_bytes.get_bytes(xref_ea - (num_insts * 4), num_insts * 4)
    if not raw or len(raw) < num_insts * 4:
        return None, None, None, "Failed to read bytes"

    insts = [int.from_bytes(raw[i:i+4], "little") for i in range(0, len(raw), 4)]
    regs = {}
    stack_slots = {}
    buf_ea = None

    for idx, inst in enumerate(insts):
        curr_pc = xref_ea - (num_insts * 4) + (idx * 4)
        d = decode_arm64_instruction(inst)
        op = d[0]

        if op in ('MOVZ', 'MOVN', 'ORR'):
            regs[d[1]] = d[2]
        elif op == 'MOV_REG':
            if d[2] in regs:
                regs[d[1]] = regs[d[2]]
        elif op == 'STR_W':
            rt, rn, imm = d[1], d[2], d[3]
            if rn == 31 and rt in regs:
                stack_slots[imm] = regs[rt]
        elif op == 'LDR_W':
            rt, rn, imm = d[1], d[2], d[3]
            if rn == 31 and imm in stack_slots:
                regs[rt] = stack_slots[imm]
        elif op == 'ADRP':
            rd, page_off = d[1], d[2]
            pc_page = curr_pc & ~0xFFF
            regs[rd] = pc_page + (page_off << 12)
        elif op == 'ADD_IMM':
            rd, rn, imm = d[1], d[2], d[3]
            if rn in regs:
                regs[rd] = regs[rn] + imm
                if rd == 0:
                    buf_ea = regs[0]

    key = regs.get(2, None)
    length = regs.get(1, None)
    if buf_ea is None and 0 in regs:
        buf_ea = regs[0]

    return key, length, buf_ea, "OK"


                                 
ALL_ITEMS = [
                                              
    {
        "ea": 0xA55EB,
        "type": "STRING",
        "key": 24,
        "enc": "7K{jqhl7[wj}MWzr}{l6",
        "dec": "/Script/CoreUObject.",
        "category": "Unreal Engine Reflection",
        "detail": "Target string for v8 buffer (length 20)"
    },
    {
        "ea": 0xA5928,
        "type": "STRING",
        "key": 24,
        "enc": "Hjwh}jla",
        "dec": "Property",
        "category": "Unreal Engine Reflection",
        "detail": "Target string for v7 buffer (length 8)"
    },
    {
        "ea": 0x3775DC,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "BL sub_2E34C4(v8, 0x14, 24)",
        "dec": "NOP (Reassembled)",
        "category": "Hex-Rays Clean Bypass",
        "detail": "Reassembled to NOP -> eliminates XOR decryption at runtime"
    },
    {
        "ea": 0x3775EC,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "BL sub_2E34C4(v7, 8, 24)",
        "dec": "NOP (Reassembled)",
        "category": "Hex-Rays Clean Bypass",
        "detail": "Reassembled to NOP -> eliminates XOR decryption at runtime"
    },
    {
        "ea": 0xA4783,
        "type": "STRING",
        "key": 24,
        "enc": "tqzM],6kw",
        "dec": "libUE4.so",
        "category": "Unreal Engine Reflection",
        "detail": "Unreal Engine 4 Core Library"
    },

                                                                                                 
    {
        "ea": 0x3827D0,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A5D96 + QWORD",
        "dec": "vkAcquireNextImageKHR",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE60 = vkGetInstanceProcAddr(..., 'vkAcquireNextImageKHR')"
    },
    {
        "ea": 0x382800,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsIm}m}Hj}k}vlSPJ",
        "dec": "vkQueuePresentKHR",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE68 = vkGetInstanceProcAddr(..., 'vkQueuePresentKHR')"
    },
    {
        "ea": 0x382828,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A37D4 + v28",
        "dec": "vkGetSwapchainImagesKHR",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE70 = vkGetInstanceProcAddr(..., 'vkGetSwapchainImagesKHR')"
    },
    {
        "ea": 0x382854,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A593E + wjaJ}imqj}u}vlk",
        "dec": "vkGetImageMemoryRequirements",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE78 = vkGetInstanceProcAddr(..., 'vkGetImageMemoryRequirements')"
    },
    {
        "ea": 0x382880,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns]vmu}jyl}Hpakq{yt\\}nq{}k",
        "dec": "vkEnumeratePhysicalDevices",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE80 = vkGetInstanceProcAddr(..., 'vkEnumeratePhysicalDevices')"
    },
    {
        "ea": 0x3828B4,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "vkGetPhysicalDeviceMemoryPropertq}k",
        "dec": "vkGetPhysicalDeviceMemoryProperties",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE88 = vkGetInstanceProcAddr(..., 'vkGetPhysicalDeviceMemoryProperties')"
    },
    {
        "ea": 0x3828E0,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsUyhU}uwja",
        "dec": "vkMapMemory",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE90 = vkGetInstanceProcAddr(..., 'vkMapMemory')"
    },
    {
        "ea": 0x382914,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsYttw{yl}U}uwja",
        "dec": "vkAllocateMemory",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CE98 = vkGetInstanceProcAddr(..., 'vkAllocateMemory')"
    },
    {
        "ea": 0x38293C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A698F + byte 97",
        "dec": "vkBindImageMemory",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEA0 = vkGetInstanceProcAddr(..., 'vkBindImageMemory')"
    },
    {
        "ea": 0x382968,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A096E + zj}kwmj{}Tyawml",
        "dec": "vkGetImageSubresourceLayout",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEA8 = vkGetInstanceProcAddr(..., 'vkGetImageSubresourceLayout')"
    },
    {
        "ea": 0x38299C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "0x7D6C797D6A5B736E + 0x7D7F7975517D6C",
        "dec": "vkCreateImage",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEB8 = vkGetInstanceProcAddr(..., 'vkCreateImage')"
    },
    {
        "ea": 0x3829C8,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A0D11 + 0x7D7F797551774C",
        "dec": "vkCmdCopyBufferToImage",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEC0 = vkGetInstanceProcAddr(..., 'vkCmdCopyBufferToImage')"
    },
    {
        "ea": 0x3829FC,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "0x776A6C6B7D5C736E + 0x7D7F7975516177",
        "dec": "vkDestroyImage",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEC8 = vkGetInstanceProcAddr(..., 'vkDestroyImage')"
    },
    {
        "ea": 0x382A30,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "xmmword_A1864 + |}jHykk",
        "dec": "vkCmdBeginRenderPass",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEB0 = vkGetInstanceProcAddr(..., 'vkCmdBeginRenderPass')"
    },
    {
        "ea": 0x382A64,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "vkGetPhysicalDeviceQueueFamilyProperties",
        "dec": "vkGetPhysicalDeviceQueueFamilyProperties",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CED0 = vkGetInstanceProcAddr(..., 'vkGetPhysicalDeviceQueueFamilyProperties')"
    },
    {
        "ea": 0x382A90,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns[j}yl}[wuuyv|Hwwt",
        "dec": "vkCreateCommandPool",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CED8 = vkGetInstanceProcAddr(..., 'vkCreateCommandPool')"
    },
    {
        "ea": 0x382ABC,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsYttw{yl}[wuuyv|Zm~~}jk",
        "dec": "vkAllocateCommandBuffers",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEE0 = vkGetInstanceProcAddr(..., 'vkAllocateCommandBuffers')"
    },
    {
        "ea": 0x382AE8,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns[u|Hqh}tqv}Zyjjq}j",
        "dec": "vkCmdPipelineBarrier",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEE8 = vkGetInstanceProcAddr(..., 'vkCmdPipelineBarrier')"
    },
    {
        "ea": 0x382B14,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "0x68775B7C755B736E + 0x7D7F7975516168",
        "dec": "vkCmdCopyImage",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEF0 = vkGetInstanceProcAddr(..., 'vkCmdCopyImage')"
    },
    {
        "ea": 0x382B3C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns_}l\\}nq{}Im}m}",
        "dec": "vkGetDeviceQueue",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CEF8 = vkGetInstanceProcAddr(..., 'vkGetDeviceQueue')"
    },
    {
        "ea": 0x382B6C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns]v|[wuuyv|Zm~~}j",
        "dec": "vkEndCommandBuffer",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF00 = vkGetInstanceProcAddr(..., 'vkEndCommandBuffer')"
    },
    {
        "ea": 0x382B98,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns[j}yl}^}v{}",
        "dec": "vkCreateFence",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF08 = vkGetInstanceProcAddr(..., 'vkCreateFence')"
    },
    {
        "ea": 0x382BC4,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsIm}m}Kmzuql",
        "dec": "vkQueueSubmit",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF10 = vkGetInstanceProcAddr(..., 'vkQueueSubmit')"
    },
    {
        "ea": 0x382BE4,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsOyql^wj^}v{}k",
        "dec": "vkWaitForFences",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF18 = vkGetInstanceProcAddr(..., 'vkWaitForFences')"
    },
    {
        "ea": 0x382C10,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns\\}kljwa^}v{}",
        "dec": "vkDestroyFence",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF20 = vkGetInstanceProcAddr(..., 'vkDestroyFence')"
    },
    {
        "ea": 0x382C3C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "nsMvuyhU}uwja",
        "dec": "vkUnmapMemory",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF28 = vkGetInstanceProcAddr(..., 'vkUnmapMemory')"
    },
    {
        "ea": 0x382C68,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "ns^j}}U}uwja",
        "dec": "vkFreeMemory",
        "category": "Vulkan Init (sub_382768)",
        "detail": "qword_57CF30 = vkGetInstanceProcAddr(..., 'vkFreeMemory')"
    },

                                                                  
    {
        "ea": 0x2F9EA4,
        "type": "CALL_NOP",
        "key": 182,
        "enc": "BL sub_2E34C4(0xA1837, 7, 182)",
        "dec": "libmono (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Mono module name deobfuscation"
    },
    {
        "ea": 0x317AF4,
        "type": "CALL_NOP",
        "key": 182,
        "enc": "BL sub_2E34C4(0xA1837, 7, 182)",
        "dec": "libmono (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Mono module name deobfuscation"
    },
    {
        "ea": 0x318638,
        "type": "CALL_NOP",
        "key": 182,
        "enc": "BL sub_2E34C4(0x9FD6A, 10, 182)",
        "dec": "libmono.so (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Mono shared object library deobfuscation"
    },
    {
        "ea": 0x328ACC,
        "type": "CALL_NOP",
        "key": 182,
        "enc": "BL sub_2E34C4(0xA1837, 7, 182)",
        "dec": "libmono (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Mono module name deobfuscation"
    },
    {
        "ea": 0x32A730,
        "type": "CALL_NOP",
        "key": 182,
        "enc": "BL sub_2E34C4(0x9FD6A, 10, 182)",
        "dec": "libmono.so (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Mono shared object library deobfuscation"
    },
    {
        "ea": 0x9FD6A,
        "type": "STRING",
        "key": 182,
        "enc": "\\xb8\\x98\\xda\\xb7\\xb5\\xb4\\xb5\\xd4\\xcd\\xb5",
        "dec": "libmono.so",
        "category": "Unity / Mono",
        "detail": "Mono runtime library (Key 0xB6 / 182)"
    },
    {
        "ea": 0xA1837,
        "type": "STRING",
        "key": 182,
        "enc": "\\xda\\xdf\\xd4\\xdb\\xd9\\xd8\\xd9",
        "dec": "libmono",
        "category": "Unity / Mono",
        "detail": "Mono module base (Key 0xB6 / 182)"
    },
    {
        "ea": 0xA3B50,
        "type": "STRING",
        "key": 24,
        "enc": "Ykk}uzta5[Kpyjh6|tt",
        "dec": "Assembly-CSharp.dll",
        "category": "Unity / Mono",
        "detail": "Unity User Game Script Assembly"
    },
    {
        "ea": 0xA117D,
        "type": "STRING",
        "key": 24,
        "enc": "7ykk}lk7zqv7\\yly7Uyvy~}|7",
        "dec": "/assets/bin/Data/Managed/",
        "category": "Unity / Mono",
        "detail": "Unity Managed DLL directory"
    },
    {
        "ea": 0x9FD46,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwGquy~}Gwh}vG~jwuG|ylyGoqlpGvyu}",
        "dec": "mono_image_open_from_data_with_name",
        "category": "Unity / Mono",
        "detail": "Mono assembly loading hook"
    },
    {
        "ea": 0xA091C,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwG{wuhqt}Gu}lpw|",
        "dec": "mono_compile_method",
        "category": "Unity / Mono",
        "detail": "Mono JIT compiler hook"
    },
    {
        "ea": 0xA337F,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwG{tykkG~jwuGvyu}",
        "dec": "mono_class_from_name",
        "category": "Unity / Mono",
        "detail": "Mono class lookup"
    },
    {
        "ea": 0xA1C07,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwG{tykkG~q}t|G~jwuGvyu}",
        "dec": "mono_class_get_field_from_name",
        "category": "Unity / Mono",
        "detail": "Mono class field inspection"
    },
    {
        "ea": 0xA2BF2,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwG|wuyqvGykk}uztaGwh}v",
        "dec": "mono_domain_assembly_open",
        "category": "Unity / Mono",
        "detail": "Mono domain assembly loader"
    },
    {
        "ea": 0xA430D,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwGykk}uztaG~wj}y{p",
        "dec": "mono_assembly_foreach",
        "category": "Unity / Mono",
        "detail": "Mono assembly iteration hook (Length: 21, Key: 0x18 / 24)"
    },
    {
        "ea": 0x31840C,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "BL sub_2E34C4(v21, 21, 24)",
        "dec": "mono_assembly_foreach (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Reassembled to NOP -> eliminates XOR decryption at runtime"
    },
    {
        "ea": 0x329314,
        "type": "CALL_NOP",
        "key": 24,
        "enc": "BL sub_2E34C4(v21, 21, 24)",
        "dec": "mono_assembly_foreach (Reassembled: NOP)",
        "category": "Unity / Mono",
        "detail": "Reassembled to NOP -> eliminates XOR decryption at runtime"
    },
    {
        "ea": 0xA50AB,
        "type": "STRING",
        "key": 24,
        "enc": "uwvwGykk}uztaGtwy|G~jwuG~mtt",
        "dec": "mono_assembly_load_from_full",
        "category": "Unity / Mono",
        "detail": "Mono assembly dynamic load hook (Key: 0x18 / 24)"
    },

                                        
    {
        "ea": 0xA591C,
        "type": "STRING",
        "key": 24,
        "enc": "tqzyvw~fk6kw",
        "dec": "libanogs.so",
        "category": "Tencent Security",
        "detail": "Main Anti-Cheat Shared Object"
    },
    {
        "ea": 0xA14DC,
        "type": "STRING",
        "key": 24,
        "enc": "pllh\"77|wov6ii6{wu7q}|ky~}7[tq}vl7yv|jwq|",
        "dec": "http://down.qq.com/iedsafe/Client/android",
        "category": "Tencent Security",
        "detail": "Tencent IED Safe CDN base URL"
    },
    {
        "ea": 0xA2117,
        "type": "STRING",
        "key": 24,
        "enc": "pllh\"77|wov6ii6{wu...7=|7=( @7ujh{k{6|yly",
        "dec": "http://down.qq.com/iedsafe/Client/android/%d/%08X/mrpcsc.data",
        "category": "Tencent Security",
        "detail": "Encrypted ruleset download URL"
    },
    {
        "ea": 0xA2FF0,
        "type": "STRING",
        "key": 24,
        "enc": "pllh\"77|wov6ii6{wu...7=|7{wv~q~*6`ut",
        "dec": "http://down.qq.com/iedsafe/Client/android/%d/config2.xml",
        "category": "Tencent Security",
        "detail": "Client XML configuration URL"
    },
    {
        "ea": 0xA3BFD,
        "type": "STRING",
        "key": 24,
        "enc": "pllh\"77...7l}kl7=|7{wv~q~*6`ut",
        "dec": "http://down.qq.com/iedsafe/Client/android/test/%d/config2.xml",
        "category": "Tencent Security",
        "detail": "Test channel XML configuration URL"
    },
    {
        "ea": 0xA098A,
        "type": "STRING",
        "key": 24,
        "enc": "ujh{k{6|yly",
        "dec": "mrpcsc.data",
        "category": "Tencent Security",
        "detail": "Protected security payload"
    },
    {
        "ea": 0xA4811,
        "type": "STRING",
        "key": 24,
        "enc": "Ujh{kY{lqn}Kq~",
        "dec": "MrpcsActiveSig",
        "category": "Tencent Security",
        "detail": "Active security signature token"
    },
    {
        "ea": 0xA6DCD,
        "type": "STRING",
        "key": 24,
        "enc": "mvbqhujh{k6|yly",
        "dec": "unzipmrpcs.data",
        "category": "Tencent Security",
        "detail": "Extracted signature payload"
    },

                                                    
    {
        "ea": 0x9F228,
        "type": "STRING",
        "key": 24,
        "enc": "7hjw{7k}t~7uyhk",
        "dec": "/proc/self/maps",
        "category": "Android System",
        "detail": "Memory map scanner (anti-hook/anti-inject)"
    },
    {
        "ea": 0xA3B64,
        "type": "STRING",
        "key": 24,
        "enc": "7hjw{7=|7uyhk",
        "dec": "/proc/%d/maps",
        "category": "Android System",
        "detail": "Process memory mapping inspection"
    },
    {
        "ea": 0x9F63E,
        "type": "STRING",
        "key": 24,
        "enc": "7hjw{7=|7{u|tqv}",
        "dec": "/proc/%d/cmdline",
        "category": "Android System",
        "detail": "Process command-line checker"
    },
    {
        "ea": 0xA47BE,
        "type": "STRING",
        "key": 24,
        "enc": "7hjw{7zmk7qvhml7|}nq{}k",
        "dec": "/proc/bus/input/devices",
        "category": "Android System",
        "detail": "Hardware input touch/mouse device checker"
    },
    {
        "ea": 0xA3032,
        "type": "STRING",
        "key": 24,
        "enc": "7|yly7|yly7=k7tqz",
        "dec": "/data/data/%s/lib",
        "category": "Android System",
        "detail": "App native library path"
    },
    {
        "ea": 0xA6DB4,
        "type": "STRING",
        "key": 24,
        "enc": "7|yly7yhh7=k5=|7zyk}6yhs",
        "dec": "/data/app/%s-%d/base.apk",
        "category": "Android System",
        "detail": "Installed APK file path"
    },
    {
        "ea": 0xA4C92,
        "type": "STRING",
        "key": 24,
        "enc": "Yv|jwq|Uyvq~}kl6`ut",
        "dec": "AndroidManifest.xml",
        "category": "Android System",
        "detail": "Android Manifest file checker"
    },
    {
        "ea": 0xA24D3,
        "type": "STRING",
        "key": 24,
        "enc": "yv|jwq|7yhh7Y{lqnqlaLpj}y|",
        "dec": "android/app/ActivityThread",
        "category": "Android System",
        "detail": "Java reflection ActivityThread"
    },
    {
        "ea": 0xA1C43,
        "type": "STRING",
        "key": 24,
        "enc": "\\x7flHy{sy\\x7f}Uyvy\\x7f}j",
        "dec": "getPackageManager",
        "category": "Android System",
        "detail": "Android PackageManager method"
    },
    {
        "ea": 0xA4CC2,
        "type": "STRING",
        "key": 24,
        "enc": "\\x7flHy{sy\\x7f}Qv~w",
        "dec": "getPackageInfo",
        "category": "Android System",
        "detail": "Android PackageInfo query"
    },
    {
        "ea": 0x9F60F,
        "type": "STRING",
        "key": 24,
        "enc": "\\x7flHy{sy\\x7f}Vyu}",
        "dec": "getPackageName",
        "category": "Android System",
        "detail": "App Package Name getter"
    },
    {
        "ea": 0xA2838,
        "type": "STRING",
        "key": 24,
        "enc": "\\x7flHy{sy\\x7f}J}kwmj{}Hylp",
        "dec": "getPackageResourcePath",
        "category": "Android System",
        "detail": "Resource path resolution"
    },
    {
        "ea": 0xA1C55,
        "type": "STRING",
        "key": 24,
        "enc": "lwm{p]n}vl",
        "dec": "touchEvent",
        "category": "Android System",
        "detail": "Screen touch interaction hook"
    },
    {
        "ea": 0xA3B44,
        "type": "STRING",
        "key": 24,
        "enc": "n}jkqwvVyu}",
        "dec": "versionName",
        "category": "Android System",
        "detail": "App version descriptor"
    }
]

def apply_patch_and_comment(item):
    """Apply patch and comment for a single item in IDA IDB"""
    ea = item["ea"]
    itype = item["type"]
    enc = item["enc"]
    dec = item["dec"]
    key = item.get("key", 24)

    if itype == "STRING":
        cmt = f"enc: {enc} [Key: 0x{key:02X} ({key})]"
        ida_bytes.set_cmt(ea, cmt, False)
        ida_bytes.set_cmt(ea, cmt, True)
        dec_bytes = dec.encode("utf-8") + b"\x00"
        ida_bytes.patch_bytes(ea, dec_bytes)
        item["status"] = "PATCHED & COMMENTED"
    elif itype == "CALL_NOP":
        ida_bytes.patch_bytes(ea, ARM64_NOP)
        cmt = f"{enc} -> {dec} [Key: 0x{key:02X} ({key})] (Reassembled: NOP)"
        ida_bytes.set_cmt(ea, cmt, False)
        item["status"] = "REASSEMBLED (NOP)"

def apply_all_patches():
    """Apply patches and comments for all items in ALL_ITEMS"""
    for item in ALL_ITEMS:
        try:
            apply_patch_and_comment(item)
        except Exception as e:
            item["status"] = f"ERR: {e}"
    
                   
    try:
        ida_kernwin.request_refresh(ida_kernwin.IWID_DISASMS)
        ida_kernwin.request_refresh(ida_kernwin.IWID_STRINGS)
        for target_func_ea in [0x377580, 0x382768]:
            f = ida_funcs.get_func(target_func_ea)
            if f:
                try:
                    import ida_hexrays
                    ida_hexrays.mark_cfunc_dirty(f.start_ea)
                except:
                    pass
    except:
        pass


def scan_all_calls_in_idb(auto_patch=True):
    """
    Automated Full-Binary Scanner:
    1. Finds all xrefs to sub_2E34C4 across the entire IDB.
    2. Decodes instructions to trace W2 (key), W1 (len), and X0 (buffer).
    3. If W2 is not statically determined, uses auto_detect_xor_key(0..255) on candidate bytes.
    4. Automatically patches BL sub_2E34C4 to ARM64_NOP and sets comments.
    5. Returns list of newly discovered items.
    """
    new_items = []
    known_eas = set(item["ea"] for item in ALL_ITEMS)
    
    ida_kernwin.msg("\n[+] Starting Universal Full-Binary Scan for all calls to sub_2E34C4...\n")
    
                                          
    call_xrefs = []
    for xref in idautils.XrefsTo(SUB_2E34C4_EA):
                        
        if xref.type in (idaapi.fl_CF, idaapi.fl_CN, idaapi.fl_JF, idaapi.fl_JN) or xref.iscode:
            if xref.frm != SUB_2E34C4_EA and xref.frm != (SUB_2E34C4_EA - 4):
                call_xrefs.append(xref.frm)
                
    call_xrefs = sorted(list(set(call_xrefs)))
    ida_kernwin.msg(f"[+] Total calls to sub_2E34C4 found: {len(call_xrefs)}\n")
    
    for xref_ea in call_xrefs:
        key, length, buf_ea, status = trace_call_arguments(xref_ea, num_insts=36)
        
                                    
        is_known = xref_ea in known_eas
        
        enc_str = "BL sub_2E34C4"
        dec_str = "Auto-Reassembled (NOP)"
        cat = "Universal Call Scanner"
        
                                                                      
        if buf_ea and 0 < (length or 0) <= 256:
            raw_bytes = ida_bytes.get_bytes(buf_ea, length)
            if raw_bytes:
                                             
                if key is not None:
                    try:
                        dec_bytes = bytes(b ^ key for b in raw_bytes)
                        dec_str = dec_bytes.decode('ascii', errors='replace')
                        enc_str = raw_bytes.decode('latin-1', errors='replace')
                    except:
                        pass
                else:
                                                            
                    cands = auto_detect_xor_key(raw_bytes)
                    if cands:
                        best = cands[0]
                        key = best["key"]
                        dec_str = best["decrypted"]
                        enc_str = raw_bytes.decode('latin-1', errors='replace')
                        cat = f"Auto-Detected Key ({best['hex_key']})"
        
        if key is None:
                                                              
            key = 24
            
        item_entry = {
            "ea": xref_ea,
            "type": "CALL_NOP",
            "key": key,
            "enc": enc_str if len(enc_str) <= 35 else (enc_str[:32] + "..."),
            "dec": dec_str,
            "category": cat,
            "detail": f"Traced: key={key} (0x{key:02X}), len={length}, buf={hex(buf_ea) if buf_ea else 'stack'}"
        }
        
        if auto_patch:
            try:
                apply_patch_and_comment(item_entry)
            except Exception as e:
                item_entry["status"] = f"ERR: {e}"
                
        if not is_known:
            ALL_ITEMS.append(item_entry)
            known_eas.add(xref_ea)
            new_items.append(item_entry)

    ida_kernwin.msg(f"[+] Scan complete! Successfully processed {len(call_xrefs)} calls across all keys.\n")
    return new_items


if HAS_QT:
    class XorResultsQtTab(ida_kernwin.PluginForm):
        """Rich Qt Dockable Tab for IDA Pro with Auto-Key Detection & Brute-Forcer"""
        WINDOW_TITLE = "XOR Decrypt & Reassemble Results"

        def OnCreate(self, form):
            self.parent = self.FormToPyQtWidget(form)
            self.init_ui()

        def init_ui(self):
            layout = QtWidgets.QVBoxLayout()
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)
            self.parent.setLayout(layout)

                                 
            header_box = QtWidgets.QFrame()
            header_box.setStyleSheet("""
                QFrame {
                    background-color: #1E232E;
                    border: 1px solid #2D3748;
                    border-radius: 8px;
                    padding: 8px;
                }
            """)
            h_layout = QtWidgets.QHBoxLayout(header_box)
            h_layout.setContentsMargins(10, 6, 10, 6)

            title_col = QtWidgets.QVBoxLayout()
            main_title = QtWidgets.QLabel("Universal XOR Decryption & Reassembly Manager")
            main_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #60A5FA;")
            sub_title = QtWidgets.QLabel("All Keys Supported (0x00 - 0xFF) | Dynamic W2 Tracer & Heuristic Guesser | Comments: Encrypted | Code: Reassembled NOP")
            sub_title.setStyleSheet("font-size: 11px; color: #94A3B8;")
            title_col.addWidget(main_title)
            title_col.addWidget(sub_title)
            h_layout.addLayout(title_col)

            h_layout.addStretch()

                         
            self.pill = QtWidgets.QLabel(f"● {len(ALL_ITEMS)} Items Managed")
            self.pill.setStyleSheet("""
                background-color: rgba(16, 185, 129, 0.2);
                color: #34D399;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 12px;
                border: 1px solid rgba(16, 185, 129, 0.4);
            """)
            h_layout.addWidget(self.pill)
            layout.addWidget(header_box)

                                                   
            autokey_box = QtWidgets.QGroupBox("Universal Auto-Key Brute-Forcer (Test Any String / Key 0..255)")
            autokey_box.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    color: #A78BFA;
                    border: 1px solid #4C1D95;
                    border-radius: 6px;
                    margin-top: 6px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    padding: 0 5px;
                }
            """)
            ak_layout = QtWidgets.QHBoxLayout(autokey_box)
            ak_layout.setContentsMargins(8, 8, 8, 8)
            ak_layout.setSpacing(8)

            self.brute_input = QtWidgets.QLineEdit()
            self.brute_input.setPlaceholderText("Enter encrypted text (e.g. 7K{jqhl7[wj}MWzr}{l6, ns]vmu}jyl}...)...")
            self.brute_input.setStyleSheet("""
                QLineEdit {
                    background-color: #0F172A;
                    border: 1px solid #4B5563;
                    border-radius: 4px;
                    padding: 5px 10px;
                    color: #F8FAFC;
                    font-size: 12px;
                }
            """)
            ak_layout.addWidget(self.brute_input, stretch=3)

            btn_brute = QtWidgets.QPushButton("Auto-Detect Key")
            btn_brute.setStyleSheet("background-color: #7C3AED; color: white; padding: 5px 12px; border-radius: 4px; font-weight: bold;")
            btn_brute.clicked.connect(self.run_auto_key_detection)
            ak_layout.addWidget(btn_brute)

            self.brute_result_lbl = QtWidgets.QLabel("Result: Ready to test any key 0x00 - 0xFF")
            self.brute_result_lbl.setStyleSheet("color: #F3F4F6; font-family: monospace; font-size: 12px; padding-left: 5px;")
            ak_layout.addWidget(self.brute_result_lbl, stretch=2)

            layout.addWidget(autokey_box)

                                              
            toolbar = QtWidgets.QHBoxLayout()
            
                             
            btn_scan_all = QtWidgets.QPushButton("⚡ Auto-Scan Entire Binary (All Keys 0x00-0xFF)")
            btn_scan_all.setStyleSheet("background-color: #8B5CF6; color: white; padding: 6px 14px; border-radius: 6px; font-weight: bold;")
            btn_scan_all.clicked.connect(self.run_full_binary_scan)
            toolbar.addWidget(btn_scan_all)

            search_lbl = QtWidgets.QLabel("Filter:")
            search_lbl.setStyleSheet("font-weight: bold; color: #E2E8F0; margin-left: 10px;")
            self.search_input = QtWidgets.QLineEdit()
            self.search_input.setPlaceholderText("Filter by address, API name, encrypted text, category...")
            self.search_input.setStyleSheet("""
                QLineEdit {
                    background-color: #0F172A;
                    border: 1px solid #334155;
                    border-radius: 6px;
                    padding: 6px 12px;
                    color: #F8FAFC;
                    font-size: 12px;
                }
            """)
            self.search_input.textChanged.connect(self.filter_table)
            toolbar.addWidget(search_lbl)
            toolbar.addWidget(self.search_input, stretch=2)

                             
            self.cat_filter = QtWidgets.QComboBox()
            self.cat_filter.addItems(["All Categories", "Vulkan Init (sub_382768)", "Unreal Engine Reflection", "Unity / Mono", "Android System", "Tencent Security", "Universal Call Scanner"])
            self.cat_filter.setStyleSheet("background-color: #1E293B; color: #E2E8F0; padding: 5px; border-radius: 4px;")
            self.cat_filter.currentIndexChanged.connect(self.filter_by_category)
            toolbar.addWidget(self.cat_filter)

            btn_jump = QtWidgets.QPushButton("Jump to Address")
            btn_jump.setStyleSheet("background-color: #2563EB; color: white; padding: 6px 14px; border-radius: 6px; font-weight: bold;")
            btn_jump.clicked.connect(self.jump_selected)
            toolbar.addWidget(btn_jump)

            btn_patch = QtWidgets.QPushButton("Re-Patch All")
            btn_patch.setStyleSheet("background-color: #059669; color: white; padding: 6px 14px; border-radius: 6px; font-weight: bold;")
            btn_patch.clicked.connect(self.re_patch_all)
            toolbar.addWidget(btn_patch)

            btn_copy = QtWidgets.QPushButton("Copy Decrypted")
            btn_copy.setStyleSheet("background-color: #475569; color: white; padding: 6px 14px; border-radius: 6px;")
            btn_copy.clicked.connect(self.copy_selected)
            toolbar.addWidget(btn_copy)

            layout.addLayout(toolbar)

                                
            self.table = QtWidgets.QTableWidget()
            self.table.setColumnCount(7)
            self.table.setHorizontalHeaderLabels(["Address", "Type", "Key", "Encrypted (Original)", "Decrypted (Reassembled)", "Category", "Status"])
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
            self.table.horizontalHeader().resizeSection(0, 110)
            self.table.horizontalHeader().resizeSection(1, 95)
            self.table.horizontalHeader().resizeSection(2, 90)
            self.table.horizontalHeader().resizeSection(3, 220)
            self.table.horizontalHeader().resizeSection(4, 270)
            self.table.horizontalHeader().resizeSection(5, 180)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setStyleSheet("""
                QTableWidget {
                    background-color: #0F172A;
                    gridline-color: #1E293B;
                    color: #F1F5F9;
                    font-size: 12px;
                    border: 1px solid #334155;
                    border-radius: 6px;
                }
                QHeaderView::section {
                    background-color: #1E293B;
                    color: #94A3B8;
                    padding: 6px;
                    font-weight: bold;
                    border: none;
                }
                QTableWidget::item:selected {
                    background-color: #1D4ED8;
                    color: white;
                }
            """)
            self.table.doubleClicked.connect(self.jump_selected)
            layout.addWidget(self.table)

            self.populate_table()

        def run_auto_key_detection(self):
            text = self.brute_input.text().strip()
            if not text:
                self.brute_result_lbl.setText("Please enter an encrypted string!")
                return
            candidates = auto_detect_xor_key(text)
            if candidates:
                best = candidates[0]
                self.brute_result_lbl.setText(f"Best: Key {best['hex_key']} ({best['key']}) -> '{best['decrypted']}' (Score: {best['score']})")
                ida_kernwin.msg(f"[Auto-Key] Tested 256 keys. Best Match: Key {best['hex_key']} ({best['key']}) -> '{best['decrypted']}'\n")
            else:
                self.brute_result_lbl.setText("No high-confidence ASCII key found.")

        def run_full_binary_scan(self):
            ida_kernwin.show_wait_box("Scanning entire binary across all keys (0x00 - 0xFF)...")
            try:
                new_items = scan_all_calls_in_idb(auto_patch=True)
            finally:
                ida_kernwin.hide_wait_box()
            self.populate_table()
            self.pill.setText(f"● {len(ALL_ITEMS)} Items Managed")
            ida_kernwin.info(f"Full-Binary Scan Complete!\nProcessed calls across all detected keys.\nTotal active items: {len(ALL_ITEMS)}")

        def populate_table(self):
            self.table.setRowCount(len(ALL_ITEMS))
            for row, item in enumerate(ALL_ITEMS):
                addr_str = f"0x{item['ea']:08X}"
                addr_item = QtWidgets.QTableWidgetItem(addr_str)
                addr_item.setForeground(QtGui.QBrush(QtGui.QColor("#60A5FA")))
                addr_item.setFont(QtGui.QFont("Courier New", 10, QtGui.QFont.Bold))
                self.table.setItem(row, 0, addr_item)

                type_item = QtWidgets.QTableWidgetItem(item["type"])
                if item["type"] == "CALL_NOP":
                    type_item.setForeground(QtGui.QBrush(QtGui.QColor("#F59E0B")))
                else:
                    type_item.setForeground(QtGui.QBrush(QtGui.QColor("#38BDF8")))
                self.table.setItem(row, 1, type_item)

                k_val = item.get("key", 24)
                key_str = f"0x{k_val:02X} ({k_val})"
                key_item = QtWidgets.QTableWidgetItem(key_str)
                key_item.setTextAlignment(QtCore.Qt.AlignCenter)
                if k_val == 182:
                    key_item.setForeground(QtGui.QBrush(QtGui.QColor("#EC4899")))                
                else:
                    key_item.setForeground(QtGui.QBrush(QtGui.QColor("#A78BFA")))
                self.table.setItem(row, 2, key_item)

                enc_item = QtWidgets.QTableWidgetItem(item["enc"])
                enc_item.setForeground(QtGui.QBrush(QtGui.QColor("#F87171")))
                enc_item.setFont(QtGui.QFont("Courier New", 10))
                self.table.setItem(row, 3, enc_item)

                dec_item = QtWidgets.QTableWidgetItem(item["dec"])
                dec_item.setForeground(QtGui.QBrush(QtGui.QColor("#34D399")))
                dec_item.setFont(QtGui.QFont("Courier New", 10, QtGui.QFont.Bold))
                self.table.setItem(row, 4, dec_item)

                cat_item = QtWidgets.QTableWidgetItem(item["category"])
                cat_item.setForeground(QtGui.QBrush(QtGui.QColor("#CBD5E1")))
                self.table.setItem(row, 5, cat_item)

                status_item = QtWidgets.QTableWidgetItem(item.get("status", "PATCHED & COMMENTED"))
                status_item.setForeground(QtGui.QBrush(QtGui.QColor("#10B981")))
                status_item.setFont(QtGui.QFont("Arial", 9, QtGui.QFont.Bold))
                self.table.setItem(row, 6, status_item)

        def filter_table(self, query):
            q = query.lower()
            cat = self.cat_filter.currentText()
            for row in range(self.table.rowCount()):
                row_cat = self.table.item(row, 5).text()
                cat_match = (cat == "All Categories" or cat == row_cat)
                
                text_match = False
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item and q in item.text().lower():
                        text_match = True
                        break
                self.table.setRowHidden(row, not (cat_match and text_match))

        def filter_by_category(self, idx):
            self.filter_table(self.search_input.text())

        def jump_selected(self):
            sel = self.table.selectedItems()
            if not sel:
                return
            row = sel[0].row()
            addr_text = self.table.item(row, 0).text()
            ea = int(addr_text, 16)
            ida_kernwin.jumpto(ea)
            f = ida_funcs.get_func(ea)
            if f:
                try:
                    import ida_hexrays
                    ida_hexrays.open_pseudocode(ea, 0)
                except:
                    pass

        def copy_selected(self):
            sel = self.table.selectedItems()
            if not sel:
                return
            row = sel[0].row()
            dec_text = self.table.item(row, 4).text()
            QtWidgets.QApplication.clipboard().setText(dec_text)
            ida_kernwin.msg(f"[XOR Tab] Copied to clipboard: {dec_text}\n")

        def re_patch_all(self):
            apply_all_patches()
            self.populate_table()
            ida_kernwin.info("Successfully re-patched all strings & NOPed calls in IDB!")

        def OnClose(self, form):
            pass

    def show_results_in_tab():
        apply_all_patches()
        form = XorResultsQtTab()
        form.Show(XorResultsQtTab.WINDOW_TITLE, options=ida_kernwin.PluginForm.WOPN_TAB)
        ida_kernwin.msg("\n[+] Universal XOR Manager & Auto-Key Tab opened successfully!\n")

else:
    def show_results_in_tab():
        apply_all_patches()
        ida_kernwin.msg("\n[+] All XOR Strings Patched & Reassembled (Qt not available)\n")

def main():
    show_results_in_tab()

if __name__ == "__main__":
    main()
