"""Opt-in diagnostic adapter; independent raw inputs, never actuator outputs."""

import hashlib
import json
import re

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import hapsic, time
from esphome.const import CONF_ID

DEPENDENCIES = ["mqtt", "time", "hapsic"]
AUTO_LOAD = ["json"]
ns = cg.esphome_ns.namespace("hapsic_diagnostics")
DiagnosticComponent = ns.class_("DiagnosticComponent", cg.PollingComponent)

SOURCE = cv.Schema({
    cv.Required("source_id"): cv.string,
    cv.Optional("reporting_verified", default=False): cv.boolean,
})


def producer_identity(value):
    value = cv.string(value)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise cv.Invalid("producer_id requires 1-64 alphanumeric, underscore or hyphen characters")
    return value


def validate_thresholds(config):
    if config["warning"] >= config["recovery"]:
        raise cv.Invalid("warning must be below recovery")
    return config


CONFIG_SCHEMA = cv.All(cv.Schema({
    cv.GenerateID(): cv.declare_id(DiagnosticComponent),
    cv.Required("controller"): cv.use_id(hapsic.HapsicController),
    cv.Required("clock"): cv.use_id(time.RealTimeClock),
    cv.Required("producer_id"): producer_identity,
    cv.Optional("input_topic", default="hapsic/telemetry/diagnostics/inputs"): cv.string,
    cv.Optional("input_producer", default="hapsic-python"): cv.string,
    cv.Optional("command_source", default="local"): cv.one_of("local", "production"),
    cv.Required("topic"): cv.string,
    cv.Required("sources"): cv.All(cv.ensure_list(SOURCE), cv.Length(min=6, max=6)),
    cv.Optional("airflow_verified", default=False): cv.boolean,
    cv.Optional("warning", default=60.0): cv.float_range(min=0, max=100),
    cv.Optional("recovery", default=65.0): cv.float_range(min=0, max=100),
}).extend(cv.polling_component_schema("5s")), validate_thresholds)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_controller(await cg.get_variable(config["controller"])))
    cg.add(var.set_clock(await cg.get_variable(config["clock"])))
    cg.add(var.set_identity(config["producer_id"], config["topic"]))
    cg.add(var.set_inputs(config["input_topic"], config["input_producer"], config["command_source"] == "production"))
    contract = "delivery-si-1:" + json.dumps(str(config), sort_keys=True)
    signature = int(hashlib.sha256(contract.encode()).hexdigest()[:8], 16)
    cg.add(var.set_config(config["airflow_verified"], config["warning"], config["recovery"], signature))
    for index, source in enumerate(config["sources"]):
        cg.add(var.set_source(index, source["source_id"], source["reporting_verified"]))
