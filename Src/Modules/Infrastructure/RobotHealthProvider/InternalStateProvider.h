/**
 * @file InternalStateProvider.h
 *
 * This file declares a module that writes the internal state
 * to a file, which is read by the DeployDialog, and the state of the back LED
 * to a second file, which is read by "bh status" and "bh dash".
 *
 * @author Thomas Röfer
 */

#pragma once

#include "Framework/Module.h"
#include "Platform/Semaphore.h"
#include "Platform/Thread.h"
#include "Representations/Infrastructure/FrameInfo.h"
#include "Representations/Infrastructure/InternalState.h"
#include "Representations/Infrastructure/LEDRequest.h"
#include "Representations/Infrastructure/SensorData/SystemSensorData.h"

MODULE(InternalStateProvider,
{,
  REQUIRES(FrameInfo),
  REQUIRES(LEDRequest),
  REQUIRES(SystemSensorData),
  USES(InternalState),
  PROVIDES(InternalState),
  DEFINES_PARAMETERS(
  {,
    (int)(-2) writerPriority, /**< The priority of the writer thread. */
    (int)(5000) writeDelay, /**< The delay between two writes in ms. */
    (int)(500) ledStableTime, /**< An LED state is reported once it has not changed for this long (in ms); hides short flashes. */
    (int)(1000) ledWriteDelay, /**< The minimum delay between two writes caused by a changed LED state (in ms). */
  }),
});

class InternalStateProvider : public InternalStateProviderBase, public Thread
{
  DECLARE_SYNC;
  unsigned timeWhenLastWritten = 0; /**< The last time when the internal state was written. */
  LEDRequest candidateLEDs; /**< The LED state that was requested last. */
  unsigned timeWhenLEDsChanged = 0; /**< When the requested LED state changed last. */
  LEDRequest stableLEDs; /**< The last LED state that was requested for at least ledStableTime. */
  bool ledsChanged = true; /**< stableLEDs differs from what was written last. */
  LEDRequest ledsToWrite; /**< The LED state handed to the writer thread (protected by SYNC). */
  Semaphore writeNow; /**< The semaphore is triggered when the background thread should write the data. */

  /**
   * This method is called when the representation provided needs to be updated.
   * @param theInternalState The representation updated.
   */
  void update(InternalState& theInternalState) override;

  /** Waits for the semaphore and then writes the data. */
  void writer();

public:
  /** Starts the writer thread. */
  InternalStateProvider();

  /** Stops the writer thread. */
  ~InternalStateProvider();
};
