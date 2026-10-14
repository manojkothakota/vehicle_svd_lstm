"""MCU #2 - Sensor / GNSS controller.
Reads the vehicle state from the powertrain (plant), adds the map position
(x, y, heading) like a GNSS+IMU unit would, and publishes it on the sensor bus
for the HPC and the gateway."""
from bus import Node
from config import T


class SensorMCU(Node):
    def __init__(self, car_id, path):
        super().__init__(f"car{car_id}.mcu_sensor")
        self.id, self.path = car_id, path
        self.sub(T.plant(car_id), self.on_plant)

    def on_plant(self, topic, m):
        x, y, h = self.path.position_at(m["s"])
        self.pub(T.sensor(self.id), {**m, "id": self.id, "x": x, "y": y, "heading": h})

    def start(self):
        pass        # event-driven
