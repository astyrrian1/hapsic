#include "components/hapsic_diagnostics/observer.h"
#include <iostream>
#include <sstream>
#include <string>

int main() {
  using namespace esphome::hapsic_diagnostics;
  Config config;
  config.verified = true;
  Observer observer(config);
  std::string line;
  double now = 1000;
  bool known = false;
  while (std::getline(std::cin, line)) {
    std::istringstream input(line);
    std::string command;
    input >> command;
    if (command == "request") {
      std::string request;
      std::getline(input, request);
      if (!request.empty())
        request.erase(0, 1);
      std::cout << (valid_request_id(request) ? "true" : "false") << '\n';
    } else if (command == "known") {
      known = true;
    } else if (command == "action") {
      std::string action;
      uint32_t revision = 0;
      input >> action >> revision;
      observer.action(action, revision, true);
    } else if (command == "restart") {
      auto state = observer.save();
      if (!observer.restore(state))
        return 2;
    } else if (command == "measure" || command == "tick" || command == "unsynced") {
      Frame frame;
      double voltage = 9.5;
      if (command != "measure")
        input >> now >> voltage;
      for (int i = 0; i < 6; i++) {
        input >> frame.samples[i].value;
        frame.samples[i].reported_at = now;
        frame.samples[i].report_id = static_cast<uint64_t>(now * 1000);
        frame.samples[i].verified = true;
        frame.samples[i].source_id = i + 1;
        if (known)
          frame.samples[i].accuracy = i == 0 || i == 2 ? 0.1 : i == 1 || i == 3 ? 0.5 : i == 4 ? 2 : 20;
      }
      frame.samples[6] = Sample{voltage, now, static_cast<uint64_t>(now * 1000), 7, true, NAN};
      if (command != "measure")
        observer.observe(frame, now, now, command != "unsynced");
      else {
        observer.measurement = observer.measure(frame, now);
        std::cout << observer.json(now) << '\n';
      }
    } else if (command == "snapshot")
      std::cout << observer.json(now) << '\n';
  }
}
