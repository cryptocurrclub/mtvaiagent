// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

contract AgentOracle is Ownable {
    enum Action { FundWallet, ProvideLiquidity, CrossShardMessage }

    struct Intent {
        Action action;
        address target;
        uint256 value;
        uint64 deadline;
        bool consumed;
    }

    mapping(address => bool) public agents;
    mapping(bytes32 => Intent) public intents;

    error Unauthorized();
    error InvalidIntent();
    error IntentAlreadyConsumed();

    event AgentUpdated(address indexed agent, bool enabled);
    event IntentSubmitted(bytes32 indexed intentHash, Action action, address indexed target, uint256 value, uint64 deadline);
    event IntentConsumed(bytes32 indexed intentHash);

    modifier onlyAgent() {
        if (!agents[msg.sender]) revert Unauthorized();
        _;
    }

    constructor(address initialOwner) Ownable(initialOwner) { agents[initialOwner] = true; }

    function setAgent(address agent, bool enabled) external onlyOwner {
        agents[agent] = enabled;
        emit AgentUpdated(agent, enabled);
    }

    function submitIntent(bytes32 intentHash, Action action, address target, uint256 value, uint64 deadline) external onlyAgent {
        if (intentHash == bytes32(0) || target == address(0) || deadline <= block.timestamp || intents[intentHash].target != address(0)) {
            revert InvalidIntent();
        }
        intents[intentHash] = Intent(action, target, value, deadline, false);
        emit IntentSubmitted(intentHash, action, target, value, deadline);
    }

    function consumeIntent(bytes32 intentHash) external onlyAgent returns (Intent memory intent) {
        intent = intents[intentHash];
        if (intent.target == address(0) || block.timestamp > intent.deadline) revert InvalidIntent();
        if (intent.consumed) revert IntentAlreadyConsumed();
        intents[intentHash].consumed = true;
        emit IntentConsumed(intentHash);
    }
}
