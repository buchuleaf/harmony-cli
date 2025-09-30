#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import CLI from './cli.js';

const programRoot = process.cwd();

render(<CLI programRoot={programRoot} />);