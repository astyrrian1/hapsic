import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import number, sensor
from esphome.const import CONF_ID

# Namespace match: esphome::csv_player
csv_player_ns = cg.esphome_ns.namespace('csv_player')
CSVPlayer = csv_player_ns.class_('CSVPlayer', cg.Component)

CONF_FILE = "file"
CONF_SPEED = "speed"
CONF_SENSOR_MAPPINGS = "sensor_mappings"
CONF_NUMBER_MAPPINGS = "number_mappings"

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(CSVPlayer),
    cv.Required(CONF_FILE): cv.string,
    cv.Optional(CONF_SPEED, default=1.0): cv.float_,
    cv.Optional(CONF_SENSOR_MAPPINGS): cv.Schema({
        cv.string: cv.use_id(sensor.Sensor),
    }),
    cv.Optional(CONF_NUMBER_MAPPINGS): cv.Schema({
        cv.string: cv.use_id(number.Number),
    }),
}).extend(cv.COMPONENT_SCHEMA)

async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_file(config[CONF_FILE]))
    cg.add(var.set_speed(config[CONF_SPEED]))

    if CONF_SENSOR_MAPPINGS in config:
        for key, value in config[CONF_SENSOR_MAPPINGS].items():
            sens = await cg.get_variable(value)
            cg.add(var.add_sensor_mapping(key, sens))

    if CONF_NUMBER_MAPPINGS in config:
        for key, value in config[CONF_NUMBER_MAPPINGS].items():
            num = await cg.get_variable(value)
            cg.add(var.add_number_mapping(key, num))
