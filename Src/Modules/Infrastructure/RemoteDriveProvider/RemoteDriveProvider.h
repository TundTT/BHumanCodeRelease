/**
 * @file RemoteDriveProvider.h
 *
 * This file declares a module that receives walk commands from the dashboard (bh dash, "Drive") over UDP.
 * The dashboard sends "drive <forward> <sideways> <rotation>" (each in [-1 .. 1]) ten times per second while
 * its Drive dialog is open, and "drive stop" when it closes. Without a command for maxWaitTimeMillis the
 * request is inactive, i.e. the robot stops driving by itself when the laptop, the page or the network fails.
 * It is only part of the thread configuration that bh deploy --guard writes, never of a match deployment.
 */

#pragma once

#include "Framework/Module.h"
#include "Representations/Infrastructure/FrameInfo.h"
#include "Representations/Infrastructure/RemoteDriveRequest.h"

#ifdef TARGET_ROBOT
#include "Network/UdpComm.h"
#endif

MODULE(RemoteDriveProvider,
{,
  REQUIRES(FrameInfo),
  PROVIDES(RemoteDriveRequest),

  LOADS_PARAMETERS(
  {,
    (int) udpPort, /**< UDP port the dashboard sends drive commands to. */
    (unsigned) maxWaitTimeMillis, /**< Without a command for this long (in ms), remote driving ends. */
  }),
});

class RemoteDriveProvider : public RemoteDriveProviderBase
{
#ifdef TARGET_ROBOT
  UdpComm socket; /**< The socket the commands arrive on. */
  char buffer[64]; /**< Buffer for one command. */
  unsigned lastCommandTime = 0; /**< When the last drive command arrived (0: none or "drive stop"). */
  Pose2f speed; /**< The speed of the last drive command. */
#endif

  void update(RemoteDriveRequest& theRemoteDriveRequest) override;

public:
  RemoteDriveProvider();
};
