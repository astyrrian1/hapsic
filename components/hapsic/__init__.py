import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, button, number, output, sensor, text_sensor
from esphome.const import CONF_ID
from esphome.core import CORE

DEPENDENCIES = []
AUTO_LOAD = ["sensor", "text_sensor", "binary_sensor", "output", "switch", "number", "button", "json"]

hapsic_ns = cg.esphome_ns.namespace("hapsic")
HapsicController = hapsic_ns.class_("HapsicController", cg.PollingComponent)

# Config keys
CONF_DUCT_TEMP = "duct_temp_sensor"
CONF_DUCT_RH = "duct_rh_sensor"
CONF_SUPPLY_FLOW = "supply_flow_sensor"
CONF_EXTRACT_FLOW = "extract_flow_sensor"
CONF_BYPASS = "bypass_sensor"
CONF_BYPASS_HA = "bypass_ha_sensor"
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
CONF_MAX_CAPACITY_SENSOR = "max_capacity_sensor"
CONF_MANUAL_RESET_BTN = "manual_reset_button"
CONF_STEAM_DAC = "steam_dac"
CONF_FAN_DAC = "fan_dac"
CONF_FSM_TEXT = "fsm_text_sensor"
CONF_FAULT_TEXT = "fault_text_sensor"

# Telemetry Keys
TELEMETRY_SENSORS = [
    "tel_feasibility_max_achievable_dp",
    "tel_feasibility_total_loss_cfm",
    "tel_loop_a_pv_room_dp",
    "tel_loop_a_error",
    "tel_loop_a_p_term",
    "tel_loop_a_i_term",
    "tel_loop_a_integrator",
    "tel_loop_a_output_target",
    "tel_loop_b_pv_duct_dp",
    "tel_loop_b_error",
    "tel_loop_b_v_ff",
    "tel_loop_b_p_term",
    "tel_loop_b_i_term",
    "tel_loop_b_integrator",
    "tel_loop_b_ideal_voltage",
    "tel_batch_stasis_timer_sec",
    "tel_batch_zero_volt_ticks",
    "tel_limiters_ceiling_volts",
    "tel_physics_duct_derivative",
    "tel_physics_structure_velocity",
    "tel_psychro_pre_steam_dp",
    "tel_psychro_outdoor_dp",
    "tel_psychro_duct_rh_ema",
    "tel_io_volts_out",
    "tel_io_steam_mass_lbs",
    "tel_health_chi_ema",
]

TELEMETRY_BINARY_SENSORS = [
    "tel_feasibility_is_infeasible",
    "tel_batch_boil_achieved",
    "tel_batch_stasis_active",
]

TELEMETRY_TEXT_SENSORS = [
    "tel_limiters_active_limit",
]

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(HapsicController),
            # Local hardware sensors
            cv.Required(CONF_DUCT_TEMP): cv.use_id(sensor.Sensor),
            cv.Required(CONF_DUCT_RH): cv.use_id(sensor.Sensor),
            cv.Required(CONF_SUPPLY_FLOW): cv.use_id(sensor.Sensor),
            cv.Required(CONF_EXTRACT_FLOW): cv.use_id(sensor.Sensor),
            # Zehnder Bypass
            cv.Required(CONF_BYPASS): cv.use_id(sensor.Sensor),
            cv.Optional(CONF_BYPASS_HA): cv.use_id(sensor.Sensor),
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
            cv.Required(CONF_MAX_CAPACITY_SENSOR): cv.use_id(sensor.Sensor),
            cv.Required(CONF_MANUAL_RESET_BTN): cv.use_id(button.Button),
            # PID Tuning (Optional)
            cv.Optional("kp_a_number"): cv.use_id(number.Number),
            cv.Optional("ki_a_number"): cv.use_id(number.Number),
            cv.Optional("kp_b_number"): cv.use_id(number.Number),
            cv.Optional("ki_b_number"): cv.use_id(number.Number),
            # Outputs
            cv.Required(CONF_STEAM_DAC): cv.use_id(output.FloatOutput),
            cv.Required(CONF_FAN_DAC): cv.use_id(output.FloatOutput),
            # Text sensors for HA visibility
            cv.Required(CONF_FSM_TEXT): cv.use_id(text_sensor.TextSensor),
            cv.Required(CONF_FAULT_TEXT): cv.use_id(text_sensor.TextSensor),
        }
    )
)

for t in TELEMETRY_SENSORS:
    CONFIG_SCHEMA = CONFIG_SCHEMA.extend({cv.Optional(t): sensor.sensor_schema()})
for t in TELEMETRY_BINARY_SENSORS:
    CONFIG_SCHEMA = CONFIG_SCHEMA.extend({cv.Optional(t): binary_sensor.binary_sensor_schema()})
for t in TELEMETRY_TEXT_SENSORS:
    CONFIG_SCHEMA = CONFIG_SCHEMA.extend({cv.Optional(t): text_sensor.text_sensor_schema()})

CONFIG_SCHEMA = CONFIG_SCHEMA.extend(cv.polling_component_schema("5s"))


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
    # Zehnder Bypass
    sens = await cg.get_variable(config[CONF_BYPASS])
    cg.add(var.set_bypass_sensor(sens))
    if CONF_BYPASS_HA in config:
        sens = await cg.get_variable(config[CONF_BYPASS_HA])
        cg.add(var.set_bypass_sensor_ha(sens))

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

    # Max capacity sensor
    sens = await cg.get_variable(config[CONF_MAX_CAPACITY_SENSOR])
    cg.add(var.set_max_capacity_sensor(sens))

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

    # Text sensors
    ts = await cg.get_variable(config[CONF_FSM_TEXT])
    cg.add(var.set_fsm_text(ts))
    ts = await cg.get_variable(config[CONF_FAULT_TEXT])
    cg.add(var.set_fault_text(ts))

    # Opt-in Telemetry Mapping
    for t in TELEMETRY_SENSORS:
        if t in config:
            sens = await sensor.new_sensor(config[t])
            cg.add(getattr(var, f"set_{t}")(sens))
    for t in TELEMETRY_BINARY_SENSORS:
        if t in config:
            sens = await binary_sensor.new_binary_sensor(config[t])
            cg.add(getattr(var, f"set_{t}")(sens))
    for t in TELEMETRY_TEXT_SENSORS:
        if t in config:
            sens = await text_sensor.new_text_sensor(config[t])
            cg.add(getattr(var, f"set_{t}")(sens))
