import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor, text_sensor, output, switch, number, button
from esphome.const import CONF_ID
from esphome.core import CORE

DEPENDENCIES = []
AUTO_LOAD = ["sensor", "text_sensor", "output", "switch", "number", "button", "json"]

hapsic_ns = cg.esphome_ns.namespace("hapsic")
HapsicController = hapsic_ns.class_("HapsicController", cg.PollingComponent)

# Config keys
CONF_DUCT_TEMP = "duct_temp_sensor"
CONF_DUCT_RH = "duct_rh_sensor"
CONF_SUPPLY_FLOW = "supply_flow_sensor"
CONF_EXTRACT_FLOW = "extract_flow_sensor"
CONF_BYPASS = "bypass_sensor"
CONF_OUTDOOR_TEMP = "outdoor_temp_sensor"
CONF_OUTDOOR_RH = "outdoor_rh_sensor"
CONF_HOUSE_TEMP = "house_temp_sensor"
CONF_HOUSE_RH = "house_rh_sensor"
CONF_EXTRACT_CAN_TEMP = "extract_can_temp_sensor"
CONF_EXTRACT_CAN_RH = "extract_can_rh_sensor"
CONF_EXTRACT_HA_TEMP = "extract_ha_temp_sensor"
CONF_EXTRACT_HA_RH = "extract_ha_rh_sensor"
CONF_SUPPLY_CAN_TEMP = "supply_can_temp_sensor"
CONF_SUPPLY_CAN_RH = "supply_can_rh_sensor"
CONF_SUPPLY_HA_TEMP = "supply_ha_temp_sensor"
CONF_SUPPLY_HA_RH = "supply_ha_rh_sensor"
CONF_TARGET_DEW_POINT = "target_dew_point_sensor"
CONF_MAX_CAPACITY_NUM = "max_capacity_number"
CONF_MANUAL_RESET_BTN = "manual_reset_button"
CONF_STEAM_DAC = "steam_dac"
CONF_FAN_DAC = "fan_dac"
CONF_SAFETY_RELAY = "safety_relay"
CONF_FSM_TEXT = "fsm_text_sensor"
CONF_FAULT_TEXT = "fault_text_sensor"

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(HapsicController),
            # Local hardware sensors
            cv.Required(CONF_DUCT_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_DUCT_RH): cv.use_id(sensor.Sensor),
            cv.Required(CONF_SUPPLY_FLOW): cv.use_id(sensor.Sensor),
            cv.Required(CONF_EXTRACT_FLOW): cv.use_id(sensor.Sensor),
            cv.Required(CONF_BYPASS): cv.use_id(sensor.Sensor),
            # Outdoor sensors
            cv.Required(CONF_OUTDOOR_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_OUTDOOR_RH): cv.use_id(sensor.Sensor),
            # Tier 1: House
            cv.Required(CONF_HOUSE_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_HOUSE_RH): cv.use_id(sensor.Sensor),
            # Tier 2: Extract
            cv.Required(CONF_EXTRACT_CAN_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_EXTRACT_CAN_RH): cv.use_id(sensor.Sensor),
            cv.Required(CONF_EXTRACT_HA_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_EXTRACT_HA_RH): cv.use_id(sensor.Sensor),
            # Tier 3: Supply
            cv.Required(CONF_SUPPLY_CAN_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_SUPPLY_CAN_RH): cv.use_id(sensor.Sensor),
            cv.Required(CONF_SUPPLY_HA_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_SUPPLY_HA_RH): cv.use_id(sensor.Sensor),
            # Target
            cv.Required(CONF_TARGET_DEW_POINT): cv.use_id(sensor.Sensor),
            # Adjustable controls
            cv.Required(CONF_MAX_CAPACITY_NUM): cv.use_id(number.Number),
            cv.Required(CONF_MANUAL_RESET_BTN): cv.use_id(button.Button),
            # PID Tuning (Optional)
            cv.Optional("kp_a_number"): cv.use_id(number.Number),
            cv.Optional("ki_a_number"): cv.use_id(number.Number),
            cv.Optional("kp_b_number"): cv.use_id(number.Number),
            cv.Optional("ki_b_number"): cv.use_id(number.Number),
            # Outputs
            cv.Required(CONF_STEAM_DAC): cv.use_id(output.FloatOutput),
            cv.Required(CONF_FAN_DAC): cv.use_id(output.FloatOutput),
            cv.Required(CONF_SAFETY_RELAY): cv.use_id(switch.Switch),
            # Text sensors for HA visibility
            cv.Required(CONF_FSM_TEXT): cv.use_id(text_sensor.TextSensor),
            cv.Required(CONF_FAULT_TEXT): cv.use_id(text_sensor.TextSensor),
        }
    )
    .extend(cv.polling_component_schema("5s"))
)


async def to_code(config):
    # Register the M5StamPLC and display libraries for PlatformIO ONLY on physical hardware
    if CORE.is_esp32:
        cg.add_library("m5stack/M5Unified", "0.2.13")
        cg.add_library("m5stack/M5GFX", "0.2.19")

    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Local hardware sensors
    sens = await cg.get_variable(config[CONF_DUCT_TEMP])
    cg.add(var.set_duct_temp_sensor(sens))
    sens = await cg.get_variable(config[CONF_DUCT_RH])
    cg.add(var.set_duct_rh_sensor(sens))

    # CAN sensors
    sens = await cg.get_variable(config[CONF_SUPPLY_FLOW])
    cg.add(var.set_supply_flow_sensor(sens))
    sens = await cg.get_variable(config[CONF_EXTRACT_FLOW])
    cg.add(var.set_extract_flow_sensor(sens))
    sens = await cg.get_variable(config[CONF_BYPASS])
    cg.add(var.set_bypass_sensor(sens))

    # Outdoor
    t = await cg.get_variable(config[CONF_OUTDOOR_TEMP])
    rh = await cg.get_variable(config[CONF_OUTDOOR_RH])
    cg.add(var.set_outdoor_sensors(t, rh))

    # Tier 1: House Avg (from HA)
    t = await cg.get_variable(config[CONF_HOUSE_TEMP])
    rh = await cg.get_variable(config[CONF_HOUSE_RH])
    cg.add(var.set_house_sensors(t, rh))

    # Tier 2: Extract Air
    t = await cg.get_variable(config[CONF_EXTRACT_CAN_TEMP])
    rh = await cg.get_variable(config[CONF_EXTRACT_CAN_RH])
    cg.add(var.set_extract_sensors_can(t, rh))

    t = await cg.get_variable(config[CONF_EXTRACT_HA_TEMP])
    rh = await cg.get_variable(config[CONF_EXTRACT_HA_RH])
    cg.add(var.set_extract_sensors_ha(t, rh))

    # Tier 3: Supply Air
    t = await cg.get_variable(config[CONF_SUPPLY_CAN_TEMP])
    rh = await cg.get_variable(config[CONF_SUPPLY_CAN_RH])
    cg.add(var.set_supply_sensors_can(t, rh))

    t = await cg.get_variable(config[CONF_SUPPLY_HA_TEMP])
    rh = await cg.get_variable(config[CONF_SUPPLY_HA_RH])
    cg.add(var.set_supply_sensors_ha(t, rh))

    # Target dew point
    sens = await cg.get_variable(config[CONF_TARGET_DEW_POINT])
    cg.add(var.set_target_dew_point_sensor(sens))

    # Max capacity number
    num = await cg.get_variable(config[CONF_MAX_CAPACITY_NUM])
    cg.add(var.set_max_capacity_number(num))

    # Manual reset button
    btn = await cg.get_variable(config[CONF_MANUAL_RESET_BTN])
    cg.add(var.set_manual_reset_button(btn))

    # PID Numbers
    if "kp_a_number" in config:
        num = await cg.get_variable(config["kp_a_number"])
        cg.add(var.set_kp_a_number(num))
    if "ki_a_number" in config:
        num = await cg.get_variable(config["ki_a_number"])
        cg.add(var.set_ki_a_number(num))
    if "kp_b_number" in config:
        num = await cg.get_variable(config["kp_b_number"])
        cg.add(var.set_kp_b_number(num))
    if "ki_b_number" in config:
        num = await cg.get_variable(config["ki_b_number"])
        cg.add(var.set_ki_b_number(num))

    # Outputs
    out = await cg.get_variable(config[CONF_STEAM_DAC])
    cg.add(var.set_steam_dac(out))
    out = await cg.get_variable(config[CONF_FAN_DAC])
    cg.add(var.set_fan_dac(out))
    sw = await cg.get_variable(config[CONF_SAFETY_RELAY])
    cg.add(var.set_safety_relay(sw))

    # Text sensors
    ts = await cg.get_variable(config[CONF_FSM_TEXT])
    cg.add(var.set_fsm_text(ts))
    ts = await cg.get_variable(config[CONF_FAULT_TEXT])
    cg.add(var.set_fault_text(ts))
