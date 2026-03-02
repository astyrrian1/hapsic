.esphome/build/hapsic-scenarios/.pioenvs/hapsic-scenarios/program > cpp_scenarios.txt 2>&1 &
PID=$!
sleep 5
kill $PID
