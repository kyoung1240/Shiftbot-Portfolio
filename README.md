# ShiftBot

ShiftBot is a desktop automation application that monitors Gmail for available work shifts and helps users respond quickly.

I built this project to solve a real problem I experienced while working jobs where shifts were offered through email. Missing a notification often meant missing an opportunity, so I designed ShiftBot to automate monitoring while providing a configurable, secure desktop application.

The project demonstrates API integration, OAuth authentication, automation, desktop development, and software engineering best practices.

## Features

- Gmail monitoring
- Automatic shift detection
- Automatic email replies
- Custom settings
- Background monitoring
- System tray support
- Configurable check interval

- ![Python](https://img.shields.io/badge/Python-3.13-blue)
- ![Platform](https://img.shields.io/badge/Platform-Windows-blue)
- ![Status](https://img.shields.io/badge/Status-Active-success)
- ![Version](https://img.shields.io/badge/Version-1.0-orange)

## Built With

- Python
- CustomTkinter
- Gmail API
- BeautifulSoup4
- PyInstaller

## Architecture

ShiftBot consists of several core components:

- Gmail API Integration
- OAuth Authentication
- Shift Detection Engine
- Configuration Manager
- Desktop UI
- Notification System

## Current Version

Version 1

## Future Plans

- SMS notifications
- Employer dashboard
- Multiple Gmail account support
- Better scheduling filters
- Calendar integration
- AI-powered shift recommendations
- Mobile companion app
  
## Roadmap

### Version 1 ✅

- Gmail monitoring
- Automatic replies
- Settings page
- System tray support

### Version 2 🚧

- SMS support
- Better filtering
- Calendar integration
- Activity logs

### Long-Term Vision 🌎

- Employer dashboard
- Mobile app
- AI shift recommendations
- Multiple email providers

## What I Learned

  During development I gained experience with:

- OAuth 2.0 authentication
- Gmail API integration
- Git and GitHub
- Desktop application development
- Error handling
- Debugging
- Version control

## Challenges

- One of the biggest challenges was handling Gmail OAuth authentication while securely storing user credentials.

- Another challenge was preventing duplicate email processing while ensuring notifications remained responsive.
  
## Requirements

- Python 3.13+
- Gmail API credentials
- Windows 11

## Installation

1. Clone the repository
2. Install dependencies
3. Add your Gmail credentials
4. Run shiftbot_app.py
## License

Private repository.
All rights reserved.
## Disclaimer

ShiftBot is intended for personal automation.
Users are responsible for complying with their employer's policies.
