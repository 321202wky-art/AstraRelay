function supportsPackagingNode(version) {
  return version === '22.23.2';
}

if (require.main === module && !supportsPackagingNode(process.versions.node)) {
  console.error(
    `AstraRelay Windows packaging requires Node.js 22.23.2; found ${process.versions.node}.`,
  );
  process.exitCode = 1;
}

module.exports = { supportsPackagingNode };
