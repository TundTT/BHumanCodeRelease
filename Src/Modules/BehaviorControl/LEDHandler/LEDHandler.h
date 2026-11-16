/**
 * @file LEDHandler.h
 * This file implements a module that generates the LEDRequest from certain representations.
 *
 * The LED on the back of the robot always shows the state the robot is in (see Docs/leds.md
 * for the colors). On top of that, there are two modes:
 * - match: The color never changes while the robot can move, because each change is a
 *          remote procedure call issued by the motion thread (see BoosterProvider). The only
 *          addition is that white blinks as long as no GameController is heard.
 * - test:  Short flashes report problems that are otherwise invisible from the outside
 *          (ball lost, battery low, joints hot).
 *
 * @author Thijs Jeffry de Haas
 */

#pragma once

#include "Representations/BehaviorControl/BehaviorStateInformation.h"
#include "Representations/Infrastructure/FrameInfo.h"
#include "Representations/Infrastructure/GameState.h"
#include "Representations/Infrastructure/LEDRequest.h"
#include "Representations/Infrastructure/RobotHealth.h"
#include "Representations/Modeling/BallModel.h"
#include "Representations/MotionControl/MotionInfo.h"
#include "Representations/MotionControl/MotionRequest.h"
#include "Framework/Module.h"

MODULE(LEDHandler,
{,
  REQUIRES(BallModel),
  REQUIRES(BehaviorStateInformation),
  REQUIRES(FrameInfo),
  REQUIRES(GameState),
  REQUIRES(MotionInfo),
  REQUIRES(MotionRequest),
  REQUIRES(RobotHealth),
  PROVIDES(LEDRequest),
  LOADS_PARAMETERS(
  {
    ENUM(Mode,
    {,
      automatic, /**< match if the Release configuration was deployed, test otherwise. */
      match,
      test,
    }),

    (Mode) mode, /**< What the LED shows in addition to the state of the robot. */
    (int) ballLostTime, /**< test mode: Blue flash while playing if the ball was not seen for this long (in ms). */
    (int) lowBatteryLevel, /**< test mode: Red flash if the battery is below this level (in %). */
    (int) blipPeriod, /**< test mode: Each kind of flash appears once per period (in ms). */
    (int) blipDuration, /**< test mode: Duration of a flash (in ms). */
  }),
});

class LEDHandler : public LEDHandlerBase
{
  void update(LEDRequest& ledRequest) override;

  /**
   * Replaces the current color by a flash in a single color. If that would hardly be visible,
   * because the current color is already dominated by the flash color, the LED is switched off instead.
   * @param ledRequest The request that is changed.
   * @param color The color of the flash.
   */
  static void flash(LEDRequest& ledRequest, LEDRequest::LED color);
};
