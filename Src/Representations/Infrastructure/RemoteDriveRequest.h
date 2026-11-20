/**
 * @file RemoteDriveRequest.h
 *
 * A walk command from the dashboard (keyboard driving). It only exists on robots deployed with the laptop
 * guard (bh deploy --guard), which switches on RemoteDriveProvider; elsewhere it is a default representation
 * and never active.
 */

#pragma once

#include "Math/Pose2f.h"
#include "Streaming/AutoStreamable.h"

STREAMABLE(RemoteDriveRequest,
{,
  (bool)(false) active, /**< Did a drive command arrive recently? */
  (Pose2f) speed, /**< Walk speed relative to the maximum: rotation, forward, sideways, each in [-1 .. 1]. */
});
