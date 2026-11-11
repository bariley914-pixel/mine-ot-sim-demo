"""Modbus access, split exactly the way OTMon splits it.

* ReadOnlyMaster - the MONITOR path (taken from OTMon's poller.py). Any
  function code that is not a read (FC 1-4) raises before a byte is sent.
* HmiWriter      - the OPERATOR path. A separate class, used only by the HMI
  control API, only for points whitelisted in mine.yaml (controls/faults/
  scenario coils), with range checks, and every write is audit-logged.
  It can be switched off entirely with OTMON_HMI_WRITES=0, which turns the
  dashboard back into a pure read-only OTMon monitor.
"""
from __future__ import annotations

import threading

import modbus_tk.defines as cst
from modbus_tk import modbus_tcp

READ_ONLY_FCS = frozenset({cst.READ_COILS, cst.READ_DISCRETE_INPUTS, cst.READ_HOLDING_REGISTERS,
                           cst.READ_INPUT_REGISTERS})
FC_BY_TYPE = {"coil": cst.READ_COILS, "discrete": cst.READ_DISCRETE_INPUTS,
              "holding": cst.READ_HOLDING_REGISTERS, "input": cst.READ_INPUT_REGISTERS}


class ReadOnlyViolation(RuntimeError):
    pass


class ReadOnlyMaster:
    """Wraps modbus_tk TcpMaster; any non-read function code raises before a byte is sent."""

    def __init__(self, host, port, timeout=1.5):
        self._m = modbus_tcp.TcpMaster(host=host, port=port, timeout_in_sec=timeout)

    def execute(self, unit, fc, addr, qty):
        if fc not in READ_ONLY_FCS:
            raise ReadOnlyViolation(f"function code {fc} is not a read; the monitor never writes")
        return self._m.execute(unit, fc, addr, qty)

    def close(self):
        try:
            self._m.close()
        except Exception:  # noqa: BLE001
            pass


class HmiWriter:
    """Operator write path (FC5 write single coil, FC6 write single register)."""

    _lock = threading.Lock()

    def __init__(self, timeout=2.0):
        self.timeout = timeout

    def write(self, host, port, unit, ptype, addr, raw):
        with self._lock:
            m = modbus_tcp.TcpMaster(host=host, port=port, timeout_in_sec=self.timeout)
            try:
                if ptype == "coil":
                    m.execute(unit, cst.WRITE_SINGLE_COIL, addr, output_value=1 if raw else 0)
                elif ptype == "holding":
                    m.execute(unit, cst.WRITE_SINGLE_REGISTER, addr, output_value=int(raw) & 0xFFFF)
                else:
                    raise ValueError(f"{ptype} registers are read-only")
            finally:
                m.close()
