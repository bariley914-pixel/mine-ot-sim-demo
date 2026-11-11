#!/usr/bin/env python3
"""Generates devices/*.xml (ot-sim device configs) for the mine demo.

The XML files are committed; edit this script and re-run it if you change the scenario,
then keep monitor/config/mine.yaml (register map) in sync. Usage:
    python3 scripts/gen_device_configs.py
"""
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "devices"
import textwrap
HDR = """<?xml version="1.0"?>
<ot-sim>
  <!-- {title}
     ot-sim device config for the mine OT demo (see README.md).
     Physics/fault logic runs in the ot-sim logic module; Modbus TCP exposes it.
     Addresses are raw 0-based Modbus PDU addresses; value = engineering value * 10^scaling.
     (Comment sits inside <ot-sim>: the C++ DNP3 module rejects anything before the root element.) -->
  <message-bus>
    <pull-endpoint>tcp://127.0.0.1:1234</pull-endpoint>
    <pub-endpoint>tcp://127.0.0.1:5678</pub-endpoint>
  </message-bus>
  <cpu>
    <module name="backplane">ot-sim-message-bus {{{{config_file}}}}</module>
    <module name="logic">ot-sim-logic-module {{{{config_file}}}}</module>
    <module name="modbus">ot-sim-modbus-module {{{{config_file}}}}</module>
{extra_mods}  </cpu>
"""
def reg(typ, addr, tag, scaling=None, dtype=None):
    dt = f' data-type="{dtype}"' if dtype else ''
    sc = f"\n      <scaling>{scaling}</scaling>" if scaling is not None else ''
    return f"""    <register type="{typ}"{dt}>
      <address>{addr}</address>
      <tag>{tag}</tag>{sc}
    </register>
"""
def device(fn, title, logic_name, program, variables, regs, extra_mods='', extra=''):
    s = HDR.format(title=title, extra_mods=extra_mods)
    prog = textwrap.dedent(program).strip('\n')
    prog = '\n'.join(('' if l.startswith('#') else '      ')+l if l.strip() else '' for l in prog.split('\n'))
    vars_ = '\n'.join(f'      <{k} tag="{t}">{v}</{k}>' for k, t, v in variables)
    s += f"""  <logic name="{logic_name}">
    <period>1s</period>
    <process-updates>true</process-updates>
    <program><![CDATA[
{prog}
    ]]></program>
    <variables>
{vars_}
    </variables>
  </logic>
  <modbus name="modbus-server" mode="server">
    <endpoint>0.0.0.0:502</endpoint>
"""
    for typ, items in regs:
        for i, (tag, scaling) in enumerate(items):
            s += reg(typ, i, tag, scaling if typ in ('input','holding') else None,
                     'int16' if typ in ('input','holding') else None)
    s += "  </modbus>\n" + extra + "</ot-sim>\n"
    (OUT / fn).write_text(s)

FEEDER = lambda tag: '''  <!-- ot-sim Modbus CLIENT: read underground feeder CB-2 state from the substation RTU,
       so a CH4 interlock trip (or an operator opening CB-2) really stops this motor. -->
  <modbus name="modbus-client-ss1" mode="client">
    <endpoint>rtu-sub-01:502</endpoint>
    <period>1s</period>
    <register type="discrete">
      <address>1</address>
      <tag>%s</tag>
    </register>
  </modbus>
''' % tag

# ---------------- CV-101 conveyor ----------------
device('plc-conveyor-01.xml', 'CV-101 main incline conveyor - VFD drive PLC', 'cv101-logic', """
# --- reset / scenario clock (t = seconds since start or reset) ---
t = reset > 0.5 ? 0.0 : t + 1
run_cmd = reset > 0.5 ? 1.0 : run_cmd
auto_faults = reset > 0.5 ? 1.0 : auto_faults
speed_sp = reset > 0.5 ? 100.0 : (speed_sp < 0 ? 0.0 : (speed_sp > 110 ? 110.0 : speed_sp))
cyc = float(int(t) % 150)
# --- fault: belt jam / overload. Manual inject = 30 s; auto scenario at 40-70 s of every 150 s ---
inj_t = reset > 0.5 ? 0.0 : (jam_cmd > 0.5 ? 30.0 : (inj_t > 0 ? inj_t - 1 : 0.0))
jam_cmd = 0.0
jam = (auto_faults > 0.5 && cyc >= 40 && cyc < 70) || inj_t > 0
# --- VFD overload trip: 12 s above 420 A trips the drive; reset coil (or auto mode after 30 s) clears it ---
ovl_t = overload ? ovl_t + 1 : 0.0
vfd_trip = (fault_reset > 0.5 || reset > 0.5) ? 0.0 : ((vfd_trip > 0.5 || ovl_t >= 12) ? 1.0 : 0.0)
trip_t = vfd_trip > 0.5 ? trip_t + 1 : 0.0
vfd_trip = (auto_faults > 0.5 && trip_t > 30) ? 0.0 : vfd_trip
fault_reset = 0.0
# --- process ---
# the drive needs the underground feeder CB-2 (read from SS-1 over Modbus) to be closed
energized = feeder_cb2 > 0.5
target = (run_cmd > 0.5 && vfd_trip < 0.5 && energized) ? speed_sp : 0.0
speed = speed + (target - speed) * 0.25
running = speed > 1
load = running ? (1150 * speed / 100) * (0.9 + 0.2 * randFloat()) : 0.0
current = running ? 60 + load * 0.25 + (jam ? 260 : 0) + randFloat() * 8 : 0.0
overload = current > 420
motor_temp = motor_temp + ((35 + current * 0.12) - motor_temp) * 0.06
belt_speed = speed * 0.035
kw = current * 0.69 * 1.732 * 0.86
reset = 0.0
""", [
 ('run_cmd','cv101.run_cmd','1'), ('speed_sp','cv101.speed_sp','100'), ('jam_cmd','cv101.jam_cmd','0'),
 ('auto_faults','cv101.auto_faults','1'), ('fault_reset','cv101.fault_reset','0'), ('reset','cv101.reset','0'),
 ('speed','cv101.speed_pct','100'), ('belt_speed','cv101.belt_speed','0'), ('load','cv101.load_tph','0'),
 ('current','cv101.motor_current','0'), ('motor_temp','cv101.motor_temp','35'), ('kw','cv101.motor_kw','0'),
 ('running','cv101.running','false'), ('overload','cv101.overload','false'), ('vfd_trip','cv101.vfd_trip','0'),
 ('jam','cv101.jam','false'), ('feeder_cb2','cv101.feeder_cb2','1'), ('energized','cv101.energized','true'),
], [
 ('input', [('cv101.speed_pct',1),('cv101.belt_speed',2),('cv101.load_tph',0),('cv101.motor_current',1),('cv101.motor_temp',1),('cv101.motor_kw',0)]),
 ('discrete', [('cv101.running',0),('cv101.overload',0),('cv101.vfd_trip',0),('cv101.jam',0),('cv101.energized',0)]),
 ('coil', [('cv101.run_cmd',0),('cv101.jam_cmd',0),('cv101.auto_faults',0),('cv101.fault_reset',0),('cv101.reset',0)]),
 ('holding', [('cv101.speed_sp',0)]),
], extra=FEEDER('cv101.feeder_cb2'))

# ---------------- P-201 dewatering pump ----------------
device('plc-dewater-01.xml', 'P-201 sump dewatering / slurry pump station PLC', 'p201-logic', """
t = reset > 0.5 ? 0.0 : t + 1
auto_mode = reset > 0.5 ? 1.0 : auto_mode
auto_faults = reset > 0.5 ? 1.0 : auto_faults
pump_cmd = reset > 0.5 ? 0.0 : pump_cmd
start_sp = reset > 0.5 ? 60.0 : start_sp
stop_sp = reset > 0.5 ? 35.0 : stop_sp
cyc = float(int(t) % 180)
# --- fault: pump motor protection trip. Manual inject = 45 s; auto scenario at 70-125 s of every 180 s ---
inj_t = reset > 0.5 ? 0.0 : (trip_cmd > 0.5 ? 45.0 : (inj_t > 0 ? inj_t - 1 : 0.0))
trip_cmd = 0.0
fault = (auto_faults > 0.5 && cyc >= 70 && cyc < 125) || inj_t > 0
# trip latches until fault_reset when auto scenarios are off; clears by itself in auto scenario mode
pump_trip = fault ? 1.0 : ((fault_reset > 0.5 || reset > 0.5 || auto_faults > 0.5) ? 0.0 : pump_trip)
fault_reset = 0.0
# --- level control (auto: start above start_sp, stop below stop_sp; manual: pump_cmd) ---
demand = auto_mode > 0.5 ? (level > start_sp || (pump_run && level > stop_sp)) : pump_cmd > 0.5
# the pump motor needs the underground feeder CB-2 (read from SS-1 over Modbus)
energized = feeder_cb2 > 0.5
pump_run = demand && pump_trip < 0.5 && energized
inflow = 38 + randFloat() * 8
outflow = pump_run ? 62 + randFloat() * 4 : 0.0
level = level + (inflow - outflow) * 0.025
level = level < 0 ? 0.0 : (level > 100 ? 100.0 : level)
high_level = level > 85
pressure = pump_run ? 820 + randFloat() * 40 : 15 + randFloat() * 5
current = pump_run ? 92 + randFloat() * 6 : 0.0
kw = current * 0.69 * 1.732 * 0.85
reset = 0.0
""", [
 ('auto_mode','p201.auto_mode','1'), ('pump_cmd','p201.pump_cmd','0'), ('trip_cmd','p201.trip_cmd','0'),
 ('auto_faults','p201.auto_faults','1'), ('fault_reset','p201.fault_reset','0'), ('reset','p201.reset','0'),
 ('start_sp','p201.start_level_sp','60'), ('stop_sp','p201.stop_level_sp','35'),
 ('level','p201.sump_level','50'), ('inflow','p201.inflow','0'), ('outflow','p201.outflow','0'),
 ('pressure','p201.discharge_pressure','0'), ('current','p201.motor_current','0'), ('kw','p201.motor_kw','0'),
 ('pump_run','p201.running','false'), ('pump_trip','p201.tripped','0'), ('high_level','p201.high_level','false'), ('feeder_cb2','p201.feeder_cb2','1'), ('energized','p201.energized','true'),
], [
 ('input', [('p201.sump_level',1),('p201.inflow',1),('p201.outflow',1),('p201.discharge_pressure',0),('p201.motor_current',1),('p201.motor_kw',0)]),
 ('discrete', [('p201.running',0),('p201.tripped',0),('p201.high_level',0),('p201.energized',0)]),
 ('coil', [('p201.auto_mode',0),('p201.pump_cmd',0),('p201.trip_cmd',0),('p201.auto_faults',0),('p201.fault_reset',0),('p201.reset',0)]),
 ('holding', [('p201.start_level_sp',0),('p201.stop_level_sp',0)]),
], extra=FEEDER('p201.feeder_cb2'))

# ---------------- F-301 main ventilation fan ----------------
device('plc-vent-01.xml', 'F-301 main mine ventilation fan PLC', 'f301-logic', """
t = reset > 0.5 ? 0.0 : t + 1
fan_run_cmd = reset > 0.5 ? 1.0 : fan_run_cmd
auto_faults = reset > 0.5 ? 1.0 : auto_faults
speed_sp = reset > 0.5 ? 90.0 : (speed_sp < 0 ? 0.0 : (speed_sp > 100 ? 100.0 : speed_sp))
cyc = float(int(t) % 200)
# --- fault: bearing degradation -> rising vibration. Manual inject = 60 s; auto at 110-170 s of every 200 s ---
inj_t = reset > 0.5 ? 0.0 : (vib_cmd > 0.5 ? 60.0 : (inj_t > 0 ? inj_t - 1 : 0.0))
vib_cmd = 0.0
bearing = (auto_faults > 0.5 && cyc >= 110 && cyc < 170) || inj_t > 0
bear_t = bearing ? bear_t + 1 : 0.0
target = fan_run_cmd > 0.5 ? speed_sp : 0.0
speed = speed + (target - speed) * 0.1
running = speed > 5
airflow = speed * 2.6 * (0.98 + randFloat() * 0.04)
dp = speed * speed * 0.18 * (0.98 + randFloat() * 0.04)
vib = running ? (1.6 + randFloat() * 0.5 + bear_t * 0.16) * speed / 100 : 0.05
vib_high = vib > 7.1
bearing_temp = bearing_temp + ((40 + bear_t * 0.5 + speed * 0.1) - bearing_temp) * 0.05
kw = speed * speed * speed / 1000000 * 950
fault_reset = 0.0
reset = 0.0
""", [
 ('fan_run_cmd','f301.run_cmd','1'), ('speed_sp','f301.speed_sp','90'), ('vib_cmd','f301.vib_cmd','0'),
 ('auto_faults','f301.auto_faults','1'), ('fault_reset','f301.fault_reset','0'), ('reset','f301.reset','0'),
 ('speed','f301.speed_pct','90'), ('airflow','f301.airflow','0'), ('dp','f301.fan_dp','0'),
 ('vib','f301.vibration','0'), ('bearing_temp','f301.bearing_temp','40'), ('kw','f301.motor_kw','0'),
 ('running','f301.running','false'), ('vib_high','f301.vib_high','false'),
], [
 ('input', [('f301.speed_pct',1),('f301.airflow',1),('f301.fan_dp',0),('f301.vibration',2),('f301.bearing_temp',1),('f301.motor_kw',0)]),
 ('discrete', [('f301.running',0),('f301.vib_high',0)]),
 ('coil', [('f301.run_cmd',0),('f301.vib_cmd',0),('f301.auto_faults',0),('f301.fault_reset',0),('f301.reset',0)]),
 ('holding', [('f301.speed_sp',0)]),
])

# ---------------- GM-401 gas monitor (reads fan airflow from F-301 over Modbus) ----------------
client_gas = """  <!-- ot-sim Modbus CLIENT: this RTU reads the main fan airflow from F-301 so methane
       dilution follows real ventilation (stop the fan from the HMI and CH4 climbs). -->
  <modbus name="modbus-client-f301" mode="client">
    <endpoint>plc-vent-01:502</endpoint>
    <period>1s</period>
    <register type="input" data-type="int16">
      <address>1</address>
      <tag>gm401.fan_airflow</tag>
      <scaling>1</scaling>
    </register>
  </modbus>
"""
device('rtu-gas-01.xml', 'GM-401 atmospheric monitoring RTU (longwall return airway)', 'gm401-logic', """
t = reset > 0.5 ? 0.0 : t + 1
auto_faults = reset > 0.5 ? 1.0 : auto_faults
cyc = float(int(t) % 240)
# --- event: methane inflow (gas outburst / feeder). Manual inject = 50 s; auto at 20-70 s of every 240 s ---
inj_t = reset > 0.5 ? 0.0 : (gas_cmd > 0.5 ? 50.0 : (inj_t > 0 ? inj_t - 1 : 0.0))
gas_cmd = 0.0
release = (auto_faults > 0.5 && cyc >= 20 && cyc < 70) || inj_t > 0
af = airflow < 10 ? 10.0 : airflow
ch4_target = 0.25 * 234 / af + (release ? 1.45 : 0.0) + randFloat() * 0.03
ch4_target = ch4_target > 5 ? 5.0 : ch4_target
ch4 = reset > 0.5 ? 0.25 : ch4 + (ch4_target - ch4) * 0.08
co = 6 + randFloat() * 2 + (release ? 4 : 0)
o2 = 20.9 - ch4 * 0.2 - randFloat() * 0.05
air_vel = af / 80
ch4_alert = ch4 >= 1.0
ch4_powercut = ch4 >= 1.5
airflow_mirror = airflow
reset = 0.0
""", [
 ('gas_cmd','gm401.gas_cmd','0'), ('auto_faults','gm401.auto_faults','1'), ('reset','gm401.reset','0'),
 ('airflow','gm401.fan_airflow','234'), ('ch4','gm401.ch4','0.25'), ('co','gm401.co','6'), ('o2','gm401.o2','20.9'),
 ('air_vel','gm401.air_velocity','0'), ('airflow_mirror','gm401.airflow_seen','0'),
 ('ch4_alert','gm401.ch4_alert','false'), ('ch4_powercut','gm401.ch4_powercut','false'),
], [
 ('input', [('gm401.ch4',2),('gm401.co',1),('gm401.o2',1),('gm401.air_velocity',2),('gm401.airflow_seen',1)]),
 ('discrete', [('gm401.ch4_alert',0),('gm401.ch4_powercut',0)]),
 ('coil', [('gm401.gas_cmd',0),('gm401.auto_faults',0),('gm401.reset',0)]),
], extra=client_gas)

# ---------------- SS-1 substation RTU (Modbus + DNP3, reads loads from the other PLCs) ----------------
def creg(t, a, tag, sc=None):
    if t in ('coil','discrete'):
        return f'    <register type="{t}">\n      <address>{a}</address>\n      <tag>{tag}</tag>\n    </register>\n'
    return f'    <register type="{t}" data-type="int16">\n      <address>{a}</address>\n      <tag>{tag}</tag>\n      <scaling>{sc}</scaling>\n    </register>\n'
def client(name, ep, regs):
    return (f'  <modbus name="{name}" mode="client">\n    <endpoint>{ep}</endpoint>\n    <period>1s</period>\n'
            + ''.join(creg(*r) for r in regs) + '  </modbus>\n')
sub_extra = """  <!-- ot-sim Modbus CLIENTS: the substation RTU reads each feeder load and the gas
       interlock from the field PLCs, like a real data concentrator. -->
""" + client('mb-client-cv101','plc-conveyor-01:502',[('input',5,'ss1.cv101_kw',0)]) \
    + client('mb-client-p201','plc-dewater-01:502',[('input',5,'ss1.p201_kw',0)]) \
    + client('mb-client-f301','plc-vent-01:502',[('input',5,'ss1.f301_kw',0)]) \
    + client('mb-client-gm401','rtu-gas-01:502',[('discrete',1,'ss1.gas_interlock')]) + """  <dnp3 name="dnp3-outstation" mode="server">
    <endpoint>0.0.0.0:20000</endpoint>
    <cold-restart-delay>15</cold-restart-delay>
    <outstation name="ss1-outstation">
      <local-address>1024</local-address>
      <remote-address>1</remote-address>
      <warm-restart-delay>5</warm-restart-delay>
"""
bins = ['ss1.cb1_closed','ss1.cb2_closed','ss1.undervoltage','ss1.gas_interlock']
for i,tg in enumerate(bins):
    sub_extra += f"""      <input type="binary">
        <address>{i}</address>
        <tag>{tg}</tag>
        <svar>Group1Var1</svar>
        <evar>Group2Var1</evar>
        <class>Class1</class>
      </input>
"""
for i,tg in enumerate(['ss1.cb1_cmd','ss1.cb2_cmd']):
    sub_extra += f"""      <output type="binary">
        <address>{i}</address>
        <tag>{tg}</tag>
        <svar>Group10Var2</svar>
        <evar>Group11Var2</evar>
        <class>Class1</class>
        <sbo>false</sbo>
      </output>
"""
for i,tg in enumerate(['ss1.voltage_kv','ss1.current_a','ss1.total_kw','ss1.power_factor','ss1.frequency','ss1.section_kw']):
    sub_extra += f"""      <input type="analog">
        <address>{i}</address>
        <tag>{tg}</tag>
        <svar>Group30Var6</svar>
        <evar>Group32Var6</evar>
        <class>Class1</class>
      </input>
"""
sub_extra += "    </outstation>\n  </dnp3>\n"
device('rtu-sub-01.xml', 'SS-1 mine substation RTU - 13.8 kV incomer CB-1 + underground section feeder CB-2', 'ss1-logic', """
t = reset > 0.5 ? 0.0 : t + 1
cb1_cmd = reset > 0.5 ? 1.0 : cb1_cmd
cb2_cmd = reset > 0.5 ? 1.0 : cb2_cmd
auto_faults = reset > 0.5 ? 1.0 : auto_faults
cyc = float(int(t) % 240)
# --- event: utility voltage sag. Manual inject = 12 s; auto at 150-162 s of every 240 s ---
inj_t = reset > 0.5 ? 0.0 : (sag_cmd > 0.5 ? 12.0 : (inj_t > 0 ? inj_t - 1 : 0.0))
sag_cmd = 0.0
sag = (auto_faults > 0.5 && cyc >= 150 && cyc < 162) || inj_t > 0
cb1 = cb1_cmd > 0.5
# methane interlock (30 CFR 75.323 style): CH4 >= 1.5 % de-energizes the underground section feeder
cb2 = cb1 && cb2_cmd > 0.5 && gas_interlock < 0.5
voltage = cb1 ? (sag ? 11.7 : 13.8) + (randFloat() - 0.5) * 0.08 : 0.0
section_kw = cb2 ? 180 + cv_kw + p_kw + randFloat() * 6 : 0.0
total_kw = cb1 ? 250 + f_kw + section_kw + randFloat() * 10 : 0.0
pf = cb1 ? 0.86 + randFloat() * 0.03 : 0.0
current = voltage > 1 ? total_kw / (1.732 * voltage * pf) : 0.0
freq = cb1 ? 60 + (randFloat() - 0.5) * 0.04 : 0.0
undervolt = cb1 && voltage < 12.4
reset = 0.0
""", [
 ('cb1_cmd','ss1.cb1_cmd','1'), ('cb2_cmd','ss1.cb2_cmd','1'), ('sag_cmd','ss1.sag_cmd','0'),
 ('auto_faults','ss1.auto_faults','1'), ('reset','ss1.reset','0'),
 ('cv_kw','ss1.cv101_kw','0'), ('p_kw','ss1.p201_kw','0'), ('f_kw','ss1.f301_kw','0'), ('gas_interlock','ss1.gas_interlock','0'),
 ('voltage','ss1.voltage_kv','13.8'), ('current','ss1.current_a','0'), ('total_kw','ss1.total_kw','0'),
 ('pf','ss1.power_factor','0'), ('freq','ss1.frequency','60'), ('section_kw','ss1.section_kw','0'),
 ('cb1','ss1.cb1_closed','true'), ('cb2','ss1.cb2_closed','true'), ('undervolt','ss1.undervoltage','false'),
], [
 ('input', [('ss1.voltage_kv',2),('ss1.current_a',1),('ss1.total_kw',0),('ss1.power_factor',3),('ss1.frequency',2),('ss1.section_kw',0)]),
 ('discrete', [('ss1.cb1_closed',0),('ss1.cb2_closed',0),('ss1.undervoltage',0),('ss1.gas_interlock',0)]),
 ('coil', [('ss1.cb1_cmd',0),('ss1.cb2_cmd',0),('ss1.sag_cmd',0),('ss1.auto_faults',0),('ss1.reset',0)]),
], extra_mods='    <module name="dnp3">ot-sim-dnp3-module {{config_file}}</module>\n', extra=sub_extra)
