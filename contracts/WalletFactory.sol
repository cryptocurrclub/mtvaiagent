// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

contract WalletVault {
    address public immutable owner;

    error NotOwner();
    error CallFailed();

    constructor(address owner_) {
        require(owner_ != address(0), "owner is zero");
        owner = owner_;
    }

    receive() external payable {}

    function execute(address target, uint256 value, bytes calldata data) external returns (bytes memory result) {
        if (msg.sender != owner) revert NotOwner();
        (bool success, bytes memory returnData) = target.call{value: value}(data);
        if (!success) revert CallFailed();
        return returnData;
    }
}

contract WalletFactory is Ownable {
    mapping(address => address[]) private walletsByOwner;

    event WalletCreated(address indexed owner, address indexed wallet, bytes32 indexed salt);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function createWallet(address walletOwner, bytes32 salt) external returns (address wallet) {
        require(walletOwner != address(0), "owner is zero");
        bytes32 scopedSalt = keccak256(abi.encode(msg.sender, walletOwner, salt));
        wallet = address(new WalletVault{salt: scopedSalt}(walletOwner));
        walletsByOwner[walletOwner].push(wallet);
        emit WalletCreated(walletOwner, wallet, scopedSalt);
    }

    function predictWallet(address walletOwner, bytes32 salt) external view returns (address) {
        bytes32 scopedSalt = keccak256(abi.encode(msg.sender, walletOwner, salt));
        bytes32 hash = keccak256(abi.encodePacked(
            bytes1(0xff), address(this), scopedSalt, keccak256(abi.encodePacked(type(WalletVault).creationCode, abi.encode(walletOwner)))
        ));
        return address(uint160(uint256(hash)));
    }

    function walletsOf(address walletOwner) external view returns (address[] memory) {
        return walletsByOwner[walletOwner];
    }
}
