/**
 * @file InternalStateProvider.cpp
 *
 * This file implements a module that writes the internal state
 * to a file, which is read by the DeployDialog, and the state of the back LED
 * to a second file, which is read by "bh status" and "bh dash".
 *
 * @author Thomas Röfer
 */

#include "InternalStateProvider.h"
#include "Framework/Settings.h"
#include "Streaming/Global.h"
#include "Streaming/OutStreams.h"
#include <string>
#ifdef TARGET_ROBOT
#include <unistd.h>
#else
#include <cstdlib>
#include <filesystem>
#include <system_error>
#endif

MAKE_MODULE(InternalStateProvider);

namespace
{
  /**
   * The directory the state files are written to: internalState.txt is read by the DeployDialog and by
   * "bh status", ledState.txt by "bh status" and "bh dash" (Util/bh/bhtool/remote.py, STATUS_SCRIPT) -
   * keep the candidates there in sync.
   * /var/volatile/tmp is created by Install/Files/install; a robot that was not installed this way
   * (e.g. stock Ubuntu on a Jetson) does not have it, but always has /run/user/<uid> while the user
   * service runs.
   * @return The directory, or an empty string if there is no directory to write to.
   */
  std::string stateDirectory()
  {
#ifdef TARGET_ROBOT
    for(const std::string& directory : {std::string("/var/volatile/tmp"), "/run/user/" + std::to_string(getuid())})
      if(access(directory.c_str(), W_OK) == 0)
        return directory;
#endif
    return std::string();
  }

  /**
   * In the simulator, "bh dash --practice" (Util/bh/bhtool/practice.py) sets BH_PRACTICE_DIR: each simulated
   * robot then writes its files to <BH_PRACTICE_DIR>/<team number>-<player number>, e.g. .../5-1. Must be
   * called from the robot's own thread (the settings are per robot).
   * @return The directory, or an empty string if there is none (always on a real robot).
   */
  std::string practiceDirectory()
  {
#ifndef TARGET_ROBOT
    const char* base = std::getenv("BH_PRACTICE_DIR");
    if(!base || !*base)
      return std::string();
    const Settings& settings = Global::getSettings();
    if(settings.teamNumber < 0 || settings.playerNumber < 0)
      return std::string();
    const std::string directory = std::string(base) + "/" + std::to_string(settings.teamNumber) + "-"
                                  + std::to_string(settings.playerNumber);
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    if(!error)
      return directory;
#endif
    return std::string();
  }
}

InternalStateProvider::InternalStateProvider() :
  practiceDir(practiceDirectory())
{
#ifdef TARGET_ROBOT
  writerStarted = true;
#else
  writerStarted = !practiceDir.empty();
#endif
  if(writerStarted)
  {
    setPriority(writerPriority);
    start(this, &InternalStateProvider::writer);
  }
}

InternalStateProvider::~InternalStateProvider()
{
  if(writerStarted)
  {
    announceStop();
    writeNow.post();
    stop();
  }
}

void InternalStateProvider::update(InternalState& theInternalState)
{
  // Only report an LED state that lasted a while, so that short flashes (LEDHandler's test mode)
  // do not make the report flicker.
  if(theLEDRequest != candidateLEDs)
  {
    candidateLEDs = theLEDRequest;
    timeWhenLEDsChanged = theFrameInfo.time;
  }
  else if(candidateLEDs != stableLEDs && theFrameInfo.getTimeSince(timeWhenLEDsChanged) >= ledStableTime)
  {
    stableLEDs = candidateLEDs;
    ledsChanged = true;
  }

  if(theFrameInfo.getTimeSince(timeWhenLastWritten) >= writeDelay
     || (ledsChanged && theFrameInfo.getTimeSince(timeWhenLastWritten) >= ledWriteDelay))
  {
    ledsChanged = false;
    {
      SYNC;
      ledsToWrite = stableLEDs;
      theInternalState.batteryLevel = theSystemSensorData.batteryLevel;
      theInternalState.maxTemperature = 0;
      for(float temperature : {theSystemSensorData.cpuTemperature, theSystemSensorData.gpuTemperature})
        if(temperature != SensorData::off && temperature > theInternalState.maxTemperature)
          theInternalState.maxTemperature = temperature;
    }
    writeNow.post();
    timeWhenLastWritten = theFrameInfo.time;
  }
}

void InternalStateProvider::writer()
{
  InternalState internalState;
  LEDRequest leds;
  while(writeNow.wait() && isRunning())
  {
    {
      SYNC;
      internalState = theInternalState;
      leds = ledsToWrite;
    }
    const std::string directory = practiceDir.empty() ? stateDirectory() : practiceDir;
    if(directory.empty())
      continue;
    {
      OutTextFile stream(directory + "/internalState.txt");
      if(stream.exists())
        stream << internalState.batteryLevel << static_cast<int>(internalState.maxTemperature);
    }
    {
      // The values of LEDRequest::State of the red, green and blue LED (remote.py: LED_STATES).
      OutTextFile stream(directory + "/ledState.txt");
      if(stream.exists())
        stream << static_cast<int>(leds.ledStates[LEDRequest::red]) << static_cast<int>(leds.ledStates[LEDRequest::green])
               << static_cast<int>(leds.ledStates[LEDRequest::blue]);
    }
  }
}
