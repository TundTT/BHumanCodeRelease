/**
 * @file InternalStateProvider.cpp
 *
 * This file implements a module that writes the internal state
 * to a file, which is read by the DeployDialog.
 *
 * @author Thomas Röfer
 */

#include "InternalStateProvider.h"
#include "Streaming/OutStreams.h"
#include <string>
#ifdef TARGET_ROBOT
#include <unistd.h>
#endif

MAKE_MODULE(InternalStateProvider);

namespace
{
  /**
   * The file the internal state is written to. It is read by the DeployDialog and by "bh status"
   * (Util/bh/bhtool/remote.py, STATUS_SCRIPT) - keep the candidates there in sync.
   * /var/volatile/tmp is created by Install/Files/install; a robot that was not installed this way
   * (e.g. stock Ubuntu on a Jetson) does not have it, but always has /run/user/<uid> while the user
   * service runs.
   * @return The path of the file, or an empty string if there is no directory to write to.
   */
  std::string stateFilePath()
  {
#ifdef TARGET_ROBOT
    for(const std::string& directory : {std::string("/var/volatile/tmp"), "/run/user/" + std::to_string(getuid())})
      if(access(directory.c_str(), W_OK) == 0)
        return directory + "/internalState.txt";
#endif
    return std::string();
  }
}

InternalStateProvider::InternalStateProvider()
{
#ifdef TARGET_ROBOT
  setPriority(writerPriority);
  start(this, &InternalStateProvider::writer);
#endif
}

InternalStateProvider::~InternalStateProvider()
{
#ifdef TARGET_ROBOT
  announceStop();
  writeNow.post();
  stop();
#endif
}

void InternalStateProvider::update(InternalState& theInternalState)
{
  if(theFrameInfo.getTimeSince(timeWhenLastWritten) >= writeDelay)
  {
    {
      SYNC;
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
  while(writeNow.wait() && isRunning())
  {
    {
      SYNC;
      internalState = theInternalState;
    }
    const std::string path = stateFilePath();
    if(path.empty())
      continue;
    OutTextFile stream(path);
    if(stream.exists())
      stream << internalState.batteryLevel << static_cast<int>(internalState.maxTemperature);
  }
}
