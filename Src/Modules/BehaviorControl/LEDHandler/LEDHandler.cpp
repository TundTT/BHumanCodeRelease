/**
 * @file LEDHandler.cpp
 * This file implements a module that generates the LEDRequest from certain representations.
 * @author Thijs Jeffry de Haas
 * @author Jesse Richter-Klug
 */

#include "LEDHandler.h"

MAKE_MODULE(LEDHandler);

void LEDHandler::update(LEDRequest& ledRequest)
{
  //reset
  FOREACH_ENUM(LEDRequest::LED, led)
    ledRequest.ledStates[led] = LEDRequest::off;

  const bool matchMode = mode == match || (mode == automatic && theRobotHealth.configuration == RobotHealth::Release);
  bool robotIsUp = true; // Is the robot past standing up, i.e. does the LED show a state of the game?
  bool waitingForGame = false; // Is the game neither running nor being prepared (initial / finished)?

  // Prepare overwrite LED
  if((theMotionInfo.isMotion(MotionPhase::prepare) || theMotionInfo.isMotion(MotionPhase::playDead)) && theMotionRequest.motion == MotionRequest::prepare)
  {
    ledRequest.ledStates[LEDRequest::red] = LEDRequest::on;
    ledRequest.ledStates[LEDRequest::blue] = LEDRequest::on;
    robotIsUp = false;
  }
  else if(theGameState.playerState == GameState::unstiff)
  {
    ledRequest.ledStates[LEDRequest::blue] = LEDRequest::blinking;
    robotIsUp = false;
  }
  else if(theGameState.playerState == GameState::calibration)
  {
    ledRequest.ledStates[LEDRequest::red] = LEDRequest::on;
    ledRequest.ledStates[LEDRequest::green] = LEDRequest::half;
  }
  else if(theGameState.isPenalized())
    ledRequest.ledStates[LEDRequest::red] = LEDRequest::on;
  else if(theGameState.isReady())
  {
    ledRequest.ledStates[LEDRequest::blue] = LEDRequest::on;
    ledRequest.ledStates[LEDRequest::green] = LEDRequest::half;
  }
  else if(theGameState.isSet())
  {
    ledRequest.ledStates[LEDRequest::red] = LEDRequest::on;
    ledRequest.ledStates[LEDRequest::green] = LEDRequest::on;
  }
  else if(theGameState.isPlaying())
    ledRequest.ledStates[LEDRequest::green] = LEDRequest::on;
  else
  {
    FOREACH_ENUM(LEDRequest::LED, led)
      ledRequest.ledStates[led] = LEDRequest::on;
    waitingForGame = true;
  }

  // If something else than prepare is requested, but prepare cannot be left yet, the LEDs blink quickly
  if(theBehaviorStateInformation.transitionFailed || theBehaviorStateInformation.remoteControlActive ||
     (theMotionInfo.isMotion(MotionPhase::prepare) && theMotionRequest.motion != MotionRequest::prepare)
     || (theMotionInfo.isMotion(MotionPhase::playDead) && theMotionRequest.motion != MotionRequest::playDead && theMotionRequest.motion != MotionRequest::prepare))
  {
    FOREACH_ENUM(LEDRequest::LED, led)
    {
      LEDRequest::State& state = ledRequest.ledStates[led];
      switch(state)
      {
        case LEDRequest::on:
        case LEDRequest::blinking:
          state = theBehaviorStateInformation.remoteControlActive ? LEDRequest::blinking : LEDRequest::fastBlinking;
          break;
        case LEDRequest::half:
        case LEDRequest::halfBlinking:
          state = theBehaviorStateInformation.remoteControlActive ? LEDRequest::halfBlinking : LEDRequest::halfFastBlinking;
          break;
        default:
          break;
      }
    }
    return; // This already demands attention. Do not mix it with other signals.
  }

  if(!robotIsUp)
    return;

  if(matchMode)
  {
    // Before a game, the most important question is whether the robot hears the GameController.
    // The robot stands still in these states, so blinking does not disturb anything.
    if(waitingForGame && !theGameState.gameControllerActive)
      FOREACH_ENUM(LEDRequest::LED, led)
        ledRequest.ledStates[led] = LEDRequest::blinking;
  }
  else if(blipPeriod > 0 && blipDuration > 0)
  {
    // Each kind of flash has its own time slot, so that two problems do not hide each other.
    const int phase = static_cast<int>(theFrameInfo.time % static_cast<unsigned>(blipPeriod));
    const bool healthProblem = theRobotHealth.batteryLevel < lowBatteryLevel ||
                               theRobotHealth.maxJointTemperatureStatus >= JointSensorData::veryHot;
    const bool ballLost = theGameState.isPlaying() && theGameState.playerState == GameState::active &&
                          theFrameInfo.getTimeSince(theBallModel.timeWhenLastSeen) > ballLostTime;
    if(healthProblem && phase < blipDuration)
      flash(ledRequest, LEDRequest::red);
    else if(ballLost && phase >= blipPeriod / 2 && phase < blipPeriod / 2 + blipDuration)
      flash(ledRequest, LEDRequest::blue);
  }
}

void LEDHandler::flash(LEDRequest& ledRequest, LEDRequest::LED color)
{
  bool dominated = ledRequest.ledStates[color] == LEDRequest::on;
  FOREACH_ENUM(LEDRequest::LED, led)
    if(led != color && ledRequest.ledStates[led] == LEDRequest::on)
      dominated = false;
  FOREACH_ENUM(LEDRequest::LED, led)
    ledRequest.ledStates[led] = led == color && !dominated ? LEDRequest::on : LEDRequest::off;
}
