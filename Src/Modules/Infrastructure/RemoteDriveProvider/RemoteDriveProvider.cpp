/**
 * @file RemoteDriveProvider.cpp
 *
 * This file implements a module that receives walk commands from the dashboard over UDP.
 */

#include "RemoteDriveProvider.h"
#include "Math/Range.h"
#include <cmath>
#include <cstdio>
#include <cstring>

MAKE_MODULE(RemoteDriveProvider);

RemoteDriveProvider::RemoteDriveProvider()
{
#ifdef TARGET_ROBOT
  VERIFY(socket.setBlocking(false));
  VERIFY(socket.bind("0.0.0.0", udpPort));
#endif
}

void RemoteDriveProvider::update(RemoteDriveRequest& theRemoteDriveRequest)
{
#ifdef TARGET_ROBOT
  int size;
  while((size = socket.read(buffer, sizeof(buffer) - 1)) > 0)
  {
    buffer[size] = 0;
    float forward, sideways, rotation;
    if(std::sscanf(buffer, "drive %f %f %f", &forward, &sideways, &rotation) == 3
       && std::isfinite(forward) && std::isfinite(sideways) && std::isfinite(rotation))
    {
      speed = Pose2f(Rangef::OneRange().limit(rotation), Rangef::OneRange().limit(forward),
                     Rangef::OneRange().limit(sideways));
      lastCommandTime = std::max(1u, theFrameInfo.time);
    }
    else if(!std::strcmp(buffer, "drive stop"))
      lastCommandTime = 0;
  }

  theRemoteDriveRequest.active = lastCommandTime != 0
                                 && theFrameInfo.getTimeSince(lastCommandTime) <= static_cast<int>(maxWaitTimeMillis);
  theRemoteDriveRequest.speed = theRemoteDriveRequest.active ? speed : Pose2f();
#else
  theRemoteDriveRequest = RemoteDriveRequest();
#endif
}
